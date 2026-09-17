from fastapi.testclient import TestClient

from app.api.verification import get_image_verifier
from app.main import app
from app.models.image import ImageCandidate, ImageCategory, VerificationStatus
from app.models.university import University
from app.services.ai_providers.base import AIProviderError


client = TestClient(app)


REAL_COMMONS_IMAGE = (
    "https://upload.wikimedia.org/wikipedia/commons/7/7f/"
    "The_Prime_Minister%2C_Shri_Narendra_Modi_visiting_the_"
    "Nazarbayev_University%2C_in_Astana%2C_Kazakhstan_on_July_07%2C_2015.jpg"
)


class SuccessfulVerifier:
    async def verify_many(
        self, images: list[ImageCandidate], university: University
    ) -> list[ImageCandidate]:
        return [
            image.model_copy(
                update={
                    "is_real_photo": True,
                    "is_relevant": True,
                    "category": ImageCategory.STUDENT_LIFE,
                    "confidence": 0.88,
                    "verification_status": VerificationStatus.CONFIRMED,
                    "verification_reason": "The source title explicitly names the university.",
                }
            )
            for image in images
        ]


class FailingVerifier:
    async def verify_many(
        self, images: list[ImageCandidate], university: University
    ) -> list[ImageCandidate]:
        raise AIProviderError("provider unavailable")


def _request_body() -> dict:
    return {
        "university": {
            "name": "Nazarbayev University",
            "city": "Astana",
            "country": "Kazakhstan",
            "official_domain": "nu.edu.kz",
        },
        "images": [
            {
                "image_url": REAL_COMMONS_IMAGE,
                "source_url": (
                    "https://commons.wikimedia.org/wiki/"
                    "File:The_Prime_Minister_visiting_Nazarbayev_University.jpg"
                ),
                "source_name": "Wikimedia Commons",
                "search_query": '"Nazarbayev University" students "Astana"',
                "title": "Prime Minister visiting Nazarbayev University",
            }
        ],
    }


def test_verify_endpoint_returns_enriched_images() -> None:
    app.dependency_overrides[get_image_verifier] = lambda: SuccessfulVerifier()
    try:
        response = client.post("/api/images/verify", json=_request_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    image = response.json()["images"][0]
    assert image["is_real_photo"] is True
    assert image["is_relevant"] is True
    assert image["verification_status"] == "confirmed"
    assert image["confidence"] == 0.88


def test_verify_endpoint_returns_503_for_provider_failure() -> None:
    app.dependency_overrides[get_image_verifier] = lambda: FailingVerifier()
    try:
        response = client.post("/api/images/verify", json=_request_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
