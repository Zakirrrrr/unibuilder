import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.models.image import ImageCandidate
from app.models.university import University
from app.models.verification import RankedImageDecision
from app.services.image_discovery import ImageDiscoveryService
from app.services.image_sources.google_images import GoogleImagesSource
from app.services.image_verifier import ImageVerifier
from app.services.ai_providers.gemini import GeminiService
from app.services.ai_providers.base import AIMalformedResponseError


@pytest.fixture
def anyio_backend():
    return "asyncio"


def candidate(index=0):
    return ImageCandidate(image_url=f"https://example.org/{index}.jpg",
        source_url="https://example.org/campus", source_name="Test",
        search_query="University campus", category="campus")


@pytest.mark.anyio
async def test_google_eight_queries_six_per_category():
    queries = []
    def handler(request):
        query = request.url.params["q"]
        queries.append(query)
        topic = query.split()[-1]
        return httpx.Response(200, json={"images_results": [
            {"original": f"https://example.org/{topic}/{i}.jpg",
             "link": f"https://example.org/{topic}", "title": topic}
            for i in range(8)]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        images = await ImageDiscoveryService([GoogleImagesSource(client, "test-key")]).search(
            University(name="Example University"), 6)
    assert len(queries) == 8
    assert len(images) == 48
    assert len({i.category for i in images}) == 8
    assert all(i.source_url and i.author is None and i.license is None and i.confidence is None for i in images)


@pytest.mark.anyio
async def test_selection_deadline_preserves_completed_category():
    class Provider:
        async def verify_group(self, images, university):
            if images[0].category == "library":
                await asyncio.sleep(1)
            return {str(i.id): RankedImageDecision(image_id=str(i.id), is_real_photo=True,
                is_relevant=True, category=i.category, confidence=.9, quality_score=.8,
                verification_status="likely", reason="Source context") for i in images}
    images = [candidate(), candidate(1).model_copy(update={"category": "library"})]
    result = await ImageVerifier(Provider()).select_many(images, University(name="Example"), timeout_seconds=.02)
    assert result[0].quality_score == .8
    assert result[1].confidence is None


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_id", [False, True])
async def test_gemini_compares_pixels_and_validates_ids(invalid_id):
    images = [candidate(i) for i in range(6)]
    output = BytesIO()
    Image.new("RGB", (16, 16)).save(output, "JPEG")
    for i in images:
        i._downloaded_bytes = output.getvalue()
    rows = [dict(image_id=str(i.id), is_real_photo=True, is_relevant=True,
                 category="campus", confidence=.9, quality_score=.8,
                 verification_status="likely", reason="Caption and pixels agree") for i in images]
    if invalid_id:
        rows[0]["image_id"] = "invented-id"
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"images": rows}))
    async with httpx.AsyncClient() as client:
        provider = GeminiService(client, api_key="test", sdk_client=SimpleNamespace(
            interactions=SimpleNamespace(create=create)), retry_attempts=1)
        if invalid_id:
            with pytest.raises(AIMalformedResponseError):
                await provider.verify_group(images, University(name="Example"))
        else:
            result = await provider.verify_group(images, University(name="Example"))
            assert set(result) == {str(i.id) for i in images}
    assert len(calls) == 1
    assert len([b for b in calls[0]["input"] if b["type"] == "image"]) == 6
