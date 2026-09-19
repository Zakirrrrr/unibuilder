import httpx
import pytest

from app.models import ImageCategory, VerificationStatus
from app.config import settings
from app.models.university import University
from app.services.image_sources.wikimedia import WikimediaSource


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_wikimedia_source_maps_urls_and_real_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["gsrnamespace"] == "6"
        assert request.url.params["gsrlimit"] == "2"
        assert request.headers["User-Agent"] == settings.wikidata_user_agent
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "pageid": 42,
                            "title": "File:University library.jpg",
                            "imageinfo": [
                                {
                                    "url": (
                                        "https://upload.wikimedia.org/library.jpg"
                                        "?utm_source=commons.wikimedia.org"
                                    ),
                                    "descriptionurl": (
                                        "https://commons.wikimedia.org/wiki/"
                                        "File:University_library.jpg"
                                    ),
                                    "mime": "image/jpeg",
                                    "mediatype": "BITMAP",
                                    "extmetadata": {
                                        "ImageDescription": {
                                            "value": "<b>Main</b> university library"
                                        },
                                        "Artist": {
                                            "value": '<a href="/wiki/User:Jane">Jane Doe</a>'
                                        },
                                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )

    university = University(name="Example University", city="Example City")
    query = '"Example University" library "Example City"'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        images = await WikimediaSource(client).search(university, query, 2)

    assert len(images) == 1
    image = images[0]
    assert str(image.source_url).startswith("https://commons.wikimedia.org/wiki/File:")
    assert str(image.image_url) == "https://upload.wikimedia.org/library.jpg"
    assert image.source_name == "Wikimedia Commons"
    assert image.search_query == query
    assert image.description == "Main university library"
    assert image.author == "Jane Doe"
    assert image.license == "CC BY-SA 4.0"
    assert image.category == ImageCategory.LIBRARY
    assert image.is_real_photo is None
    assert image.is_relevant is None
    assert image.verification_status == VerificationStatus.PENDING


@pytest.mark.anyio
async def test_unknown_metadata_remains_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "title": "File:Campus.jpg",
                            "imageinfo": [
                                {
                                    "url": "https://upload.wikimedia.org/campus.jpg",
                                    "descriptionurl": (
                                        "https://commons.wikimedia.org/wiki/File:Campus.jpg"
                                    ),
                                    "mime": "image/jpeg",
                                    "mediatype": "BITMAP",
                                    "extmetadata": {
                                        "Artist": {"value": "Unknown author"}
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )

    university = University(name="Example University")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        image = (await WikimediaSource(client).search(university, "campus", 1))[0]

    assert image.author is None
    assert image.license is None
    assert image.description is None


@pytest.mark.anyio
async def test_wikimedia_source_retries_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(delay: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"query": {"pages": []}})

    monkeypatch.setattr("app.services.image_sources.wikimedia.asyncio.sleep", no_sleep)
    university = University(name="Example University")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        images = await WikimediaSource(client).search(university, "campus", 1)

    assert attempts == 2
    assert images == []
