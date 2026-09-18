import httpx
import pytest

from app.models import ResolutionStatus
from app.services.university_resolver import UniversityResolver, UniversityResolverError
from app.utils.text import normalize_query


def _claim(item_id: str) -> dict:
    return {
        "mainsnak": {
            "datavalue": {"value": {"entity-type": "item", "id": item_id}}
        }
    }


def _website_claim(url: str) -> dict:
    return {"mainsnak": {"datavalue": {"value": url}}}


def _entity(item_id: str, name: str, aliases: list[str]) -> dict:
    return {
        "id": item_id,
        "labels": {"en": {"language": "en", "value": name}},
        "aliases": {
            "en": [
                {"language": "en", "value": alias} for alias in aliases
            ]
        },
        "claims": {
            "P31": [_claim("Q3918")],
            "P131": [_claim("Q_CITY")],
            "P17": [_claim("Q_COUNTRY")],
            "P856": [_website_claim("https://www.example.edu/about")],
        },
    }


def _transport(search_entities: list[dict]) -> httpx.MockTransport:
    entities = {entity["id"]: entity for entity in search_entities}
    related = {
        "Q_CITY": {
            "id": "Q_CITY",
            "labels": {"en": {"language": "en", "value": "Example City"}},
        },
        "Q_COUNTRY": {
            "id": "Q_COUNTRY",
            "labels": {"en": {"language": "en", "value": "Example Country"}},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        if action == "wbsearchentities":
            return httpx.Response(
                200,
                json={
                    "search": [
                        {
                            "id": entity["id"],
                            "label": entity["labels"]["en"]["value"],
                            "aliases": [
                                alias["value"]
                                for alias in entity.get("aliases", {}).get("en", [])
                            ],
                        }
                        for entity in search_entities
                    ]
                },
            )
        requested = request.url.params["ids"].split("|")
        available = entities | related
        return httpx.Response(
            200,
            json={"entities": {key: available[key] for key in requested if key in available}},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_normalize_query() -> None:
    assert normalize_query("  Nazarbayev\t University  ") == "Nazarbayev University"


@pytest.mark.anyio
async def test_resolves_exact_alias_and_builds_structured_university() -> None:
    entity = _entity("Q1", "Nazarbayev University", ["NU"])
    async with httpx.AsyncClient(transport=_transport([entity])) as client:
        result = await UniversityResolver(client).resolve("  NU ")

    assert result.status == ResolutionStatus.RESOLVED
    assert result.university is not None
    assert result.university.name == "Nazarbayev University"
    assert result.university.city == "Example City"
    assert result.university.country == "Example Country"
    assert result.university.official_domain == "example.edu"


@pytest.mark.anyio
async def test_returns_ambiguous_for_duplicate_exact_alias() -> None:
    first = _entity("Q1", "First Institute", ["MIT"])
    second = _entity("Q2", "Second Institute", ["MIT"])
    async with httpx.AsyncClient(transport=_transport([first, second])) as client:
        result = await UniversityResolver(client).resolve("MIT")

    assert result.status == ResolutionStatus.AMBIGUOUS
    assert len(result.candidates) == 2


@pytest.mark.anyio
async def test_returns_not_found_when_search_is_empty() -> None:
    async with httpx.AsyncClient(transport=_transport([])) as client:
        result = await UniversityResolver(client).resolve("Definitely Missing University")

    assert result.status == ResolutionStatus.NOT_FOUND


@pytest.mark.anyio
async def test_converts_timeout_to_resolver_error() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        with pytest.raises(UniversityResolverError):
            await UniversityResolver(client).resolve("MIT")


@pytest.mark.anyio
async def test_get_entities_chunks_requests_at_wikidata_limit() -> None:
    request_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested = request.url.params["ids"].split("|")
        request_sizes.append(len(requested))
        return httpx.Response(
            200,
            json={"entities": {item_id: {"id": item_id} for item_id in requested}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        entities = await UniversityResolver(client)._get_entities(
            [f"Q{index}" for index in range(1, 102)]
        )

    assert request_sizes == [50, 50, 1]
    assert len(entities) == 101


@pytest.mark.anyio
async def test_retries_wikidata_maxlag_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def no_sleep(delay: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                json={"error": {"code": "maxlag", "info": "server is lagged"}},
            )
        return httpx.Response(200, json={"search": []})

    monkeypatch.setattr("app.services.university_resolver.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await UniversityResolver(client)._request(
            {"action": "wbsearchentities", "search": "MIT", "language": "en"}
        )

    assert attempts == 2
    assert payload == {"search": []}


@pytest.mark.anyio
async def test_interactive_request_omits_maxlag():
    def handler(request):
        assert "maxlag" not in request.url.params
        return httpx.Response(200, json={"search": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await UniversityResolver(client).resolve("Example")


@pytest.mark.anyio
@pytest.mark.parametrize("name,website,expected", [
    ("Astana IT University", True, ResolutionStatus.RESOLVED),
    ("Example School", True, ResolutionStatus.NOT_FOUND),
    ("University High School", True, ResolutionStatus.NOT_FOUND),
    ("Example University", False, ResolutionStatus.NOT_FOUND),
])
async def test_broad_education_type_requires_corroboration(name, website, expected):
    entity = _entity("Q133811858", name, [])
    entity["claims"]["P31"] = [_claim("Q2385804")]
    if not website:
        entity["claims"].pop("P856")
    async with httpx.AsyncClient(transport=_transport([entity])) as client:
        result = await UniversityResolver(client).resolve(name)
    assert result.status == expected


@pytest.mark.anyio
async def test_broad_type_does_not_override_ambiguous_alias():
    entities = [_entity("Q1", "First University", ["AITU"]),
                _entity("Q2", "Second University", ["AITU"])]
    for entity in entities:
        entity["claims"]["P31"] = [_claim("Q2385804")]
    async with httpx.AsyncClient(transport=_transport(entities)) as client:
        result = await UniversityResolver(client).resolve("AITU")
    assert result.status == ResolutionStatus.AMBIGUOUS


@pytest.mark.anyio
async def test_resolved_query_cache_returns_independent_copy():
    entity = _entity("Q1", "Nazarbayev University", ["NU"])
    async with httpx.AsyncClient(transport=_transport([entity])) as client:
        resolver = UniversityResolver(client)
        first = await resolver.resolve("NU")
        first.university.name = "mutated"
        async def fail(*args, **kwargs):
            raise AssertionError("Cache must avoid another search")
        resolver._search = fail
        second = await resolver.resolve(" NU ")
        assert second.university.name == "Nazarbayev University"
