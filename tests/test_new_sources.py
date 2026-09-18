import asyncio

import httpx
import pytest

from app.models.university import University
from app.models.image import ImageCandidate
from app.services.image_sources.official import OfficialWebsiteSource
from app.services.image_sources.openverse import OpenverseSource
from app.services.image_verifier import ImageVerifier
from app.services.image_sources.google_places import GooglePlacesSource
from app.models.verification import ImageVerificationDecision


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_official_source_preserves_page_and_unknown_rights():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text='''
            <title>Example University</title>
            <img src="/campus.jpg" alt="University campus">
            <img src="/logo.svg" alt="Logo">
        ''')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        images = await OfficialWebsiteSource(client).search(
            University(name="Example University", official_domain="example.edu"), "campus", 2
        )
    assert len(images) == 1
    assert str(images[0].source_url) == "https://example.edu/"
    assert images[0].license is None
    assert images[0].is_relevant is None
    assert images[0].verification_status == "pending"


@pytest.mark.anyio
async def test_openverse_uses_original_source_page():
    def handler(request):
        return httpx.Response(200, json={"results": [{
            "url": "https://images.example.org/photo.jpg",
            "foreign_landing_url": "https://example.org/photos/1",
            "source": "flickr", "title": "Harvard Yard", "creator": "Jane",
            "license": "by",
        }]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        images = await OpenverseSource(client).search(University(name="Harvard University"), "Harvard", 2)
    assert str(images[0].source_url) == "https://example.org/photos/1"
    assert images[0].author == "Jane"
    assert images[0].verification_status == "pending"


@pytest.mark.anyio
async def test_verification_deadline_keeps_finished_images():
    class Verifier(ImageVerifier):
        async def verify(self, image, university):
            if image.title == "slow":
                await asyncio.sleep(10)
            return image.model_copy(update={"confidence": .9, "verification_status": "likely"})
    images = [ImageCandidate(
        image_url="https://example.org/a.jpg", source_url="https://example.org/page",
        source_name="test", search_query="test", title=title,
    ) for title in ("fast", "slow")]
    result = await Verifier(None).verify_many(images, University(name="Example"), timeout_seconds=.02)
    assert result[0].confidence == .9
    assert result[1].confidence is None
    assert result[1].verification_status == "uncertain"


@pytest.mark.anyio
async def test_google_photos_keep_attribution_and_do_not_expose_key():
    def handler(request):
        assert request.headers['X-Goog-Api-Key'] == 'test-only'
        assert 'test-only' not in str(request.url)
        if request.method == 'POST':
            return httpx.Response(200, json={'places': [{
                'displayName': {'text': 'Example Campus'},
                'formattedAddress': 'A different street',
                'googleMapsUri': 'https://maps.google.com/?cid=123',
                'photos': [{'name': 'places/123/photos/abc',
                            'authorAttributions': [{'displayName': 'Jane'}]}],
            }]})
        return httpx.Response(200, json={'photoUri': 'https://images.example.org/photo.jpg'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GooglePlacesSource(client, 'test-only').search(
            University(name='Example University'), 'Example', 2)
    assert result[0].author == 'Jane'
    assert result[0].verification_status == 'pending'
    assert result[0].license is None
    assert 'test-only' not in result[0].model_dump_json()


def test_ai_can_identify_campus_without_exact_metadata_match():
    image = ImageCandidate(image_url='https://example.org/photo.jpg',
                           source_url='https://example.org/review', source_name='Review',
                           search_query='campus', title='Weekend visit')
    decision = ImageVerificationDecision(is_real_photo=True, is_relevant=True,
        category='campus', confidence=.94, verification_status='confirmed',
        reason='Readable university signage identifies the campus.')
    assert ImageVerifier._verification_status(decision, image, University(name='Harvard')) == 'confirmed'
