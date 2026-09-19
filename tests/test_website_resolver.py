import json

import httpx
import pytest

from app.models.resolution import ResolutionStatus, UniversityResolutionResponse
from app.models.university import University
from app.services.university_resolver import UniversityResolver, UniversityResolverError
from app.services.website_resolver import IdentityPage, WebsiteUniversityResolver


@pytest.fixture
def anyio_backend():
    return "asyncio"


def page(domain="example.edu", name="Example University"):
    schema = {"@type": "CollegeOrUniversity", "name": name,
              "url": "https://" + domain, "alternateName": "EU",
              "address": {"addressLocality": "Example City", "addressCountry": "Exampleland"}}
    return '<script type="application/ld+json">' + json.dumps(schema) + '</script>'


def test_structured_identity_and_real_metadata():
    parser = IdentityPage()
    parser.feed(page())
    result = WebsiteUniversityResolver.identify(parser, "EU", "https://example.edu/")
    assert result.name == "Example University"
    assert result.city == "Example City"
    assert result.resolution_source == "official_website"
    assert result.evidence_urls == ["https://example.edu/"]


def test_directory_mention_is_not_official_identity():
    parser = IdentityPage()
    parser.feed(page())
    assert WebsiteUniversityResolver.identify(parser, "EU", "https://directory.org/") is None


def test_html_requires_full_name_academic_domain_and_teaching_signals():
    parser = IdentityPage()
    parser.feed('<title>Example University | Home</title>Admissions Bachelor Master')
    assert WebsiteUniversityResolver.identify(parser, "Example University", "https://example.edu/")
    assert WebsiteUniversityResolver.identify(parser, "EU", "https://example.edu/") is None
    assert WebsiteUniversityResolver.identify(parser, "Example University", "https://directory.org/") is None


def test_official_academic_title_can_verify_matching_acronym():
    parser = IdentityPage()
    parser.feed("<title>MIT - Massachusetts Institute of Technology</title>Admissions")
    result = WebsiteUniversityResolver.identify(parser, "MIT", "https://www.mit.edu/")
    assert result is not None
    assert result.name == "Massachusetts Institute of Technology"
    assert result.aliases == ["MIT"]
    assert WebsiteUniversityResolver.identify(parser, "MIT", "https://www.other.edu/") is None


@pytest.mark.anyio
async def test_web_search_keeps_multiple_verified_candidates_ambiguous(monkeypatch):
    async def public(host):
        return True
    monkeypatch.setattr("app.services.website_resolver.public_host", public)
    def handler(request):
        if request.url.host == "serpapi.com":
            return httpx.Response(200, json={"organic_results": [
                {"link": "https://first.edu/"}, {"link": "https://second.edu/"}]})
        return httpx.Response(200, text=page(request.url.host), headers={"Content-Type": "text/html"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await WebsiteUniversityResolver(client, "test-key").resolve("Example University")
    assert result.status == ResolutionStatus.AMBIGUOUS
    assert len(result.candidates) == 2


@pytest.mark.anyio
async def test_private_destination_is_not_fetched(monkeypatch):
    async def private(host):
        return False
    monkeypatch.setattr("app.services.website_resolver.public_host", private)
    def handler(request):
        raise AssertionError("Must not fetch private destination")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await WebsiteUniversityResolver(client, "test")._candidate("https://127.0.0.1/", "Example") is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [False, True])
async def test_fallback_works_when_wikidata_missing_or_unavailable(failure):
    class Website:
        async def resolve(self, query):
            return UniversityResolutionResponse(status="resolved", university=University(
                name=query, official_domain="example.edu", resolution_source="official_website"))
    async with httpx.AsyncClient() as client:
        resolver = UniversityResolver(client, website_resolver=Website())
        async def primary(query):
            if failure:
                raise UniversityResolverError("offline")
            return UniversityResolutionResponse(status="not_found")
        resolver._resolve_wikidata = primary
        result = await resolver.resolve("Example University")
    assert result.university.resolution_source == "official_website"


@pytest.mark.anyio
async def test_wikidata_ambiguity_is_not_overridden():
    class Website:
        async def resolve(self, query):
            raise AssertionError("Known ambiguity must not be bypassed")
    async with httpx.AsyncClient() as client:
        resolver = UniversityResolver(client, website_resolver=Website())
        async def primary(query):
            return UniversityResolutionResponse(status="ambiguous", candidates=[University(name="First"), University(name="Second")])
        resolver._resolve_wikidata = primary
        assert (await resolver.resolve("EU")).status == ResolutionStatus.AMBIGUOUS
