import os

import httpx
import pytest

from app.config import settings
from app.models.image import ImageCandidate, VerificationStatus
from app.models.university import University
from app.services.ai_providers.gemini import GeminiService
from app.services.image_verifier import ImageVerifier


RUN_LIVE = os.getenv("RUN_LIVE_AI_TESTS") == "1" and bool(
    settings.gemini_api_key
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.skipif(
    not RUN_LIVE,
    reason="set GEMINI_API_KEY in .env and RUN_LIVE_AI_TESTS=1",
)
@pytest.mark.anyio
async def test_live_gemini_verification_on_real_commons_images() -> None:
    university = University(
        name="Nazarbayev University",
        city="Astana",
        country="Kazakhstan",
        official_domain="nu.edu.kz",
    )
    images = [
        ImageCandidate(
            image_url=(
                "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/"
                "Gfp-wisconsin-madison-the-nature-boardwalk.jpg/"
                "1024px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
            ),
            source_url=(
                "https://commons.wikimedia.org/wiki/"
                "File:Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
            ),
            source_name="Wikimedia Commons",
            search_query="Nazarbayev University campus",
            title="Nature boardwalk in Wisconsin",
            description="A nature boardwalk in Madison, Wisconsin.",
        ),
        ImageCandidate(
            image_url=(
                "https://upload.wikimedia.org/wikipedia/commons/3/3f/"
                "Fronalpstock_big.jpg"
            ),
            source_url=(
                "https://commons.wikimedia.org/wiki/File:Fronalpstock_big.jpg"
            ),
            source_name="Wikimedia Commons",
            search_query="Nazarbayev University building",
            title="Fronalpstock mountain landscape",
            description="Mountain landscape in Switzerland.",
        ),
    ]

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.wikidata_user_agent}
    ) as client:
        service = GeminiService(client)
        try:
            results = await ImageVerifier(service).verify_many(images, university)
        finally:
            service.close()

    assert len(results) == 2
    assert all(result.confidence is not None for result in results)
    assert all(result.verification_reason for result in results)
    assert all(
        result.verification_status
        in {VerificationStatus.UNCERTAIN, VerificationStatus.REJECTED}
        for result in results
    )
