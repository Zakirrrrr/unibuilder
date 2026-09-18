from fastapi.testclient import TestClient

from app.api.profiles import get_profile_pipeline
from app.main import app
from app.models.image import ImageCandidate, ImageCategory, VerificationStatus
from app.models.profile import ProfileStatistics, UniversityProfile
from app.models.university import University
from app.services.profile_pipeline import ProfileNotFoundError


client = TestClient(app)


class SuccessfulPipeline:
    async def generate(self, query: str) -> UniversityProfile:
        image = ImageCandidate(
            image_url="https://upload.wikimedia.org/campus.jpg",
            source_url="https://commons.wikimedia.org/wiki/File:Campus.jpg",
            source_name="Wikimedia Commons",
            search_query=f"{query} campus",
            category=ImageCategory.CAMPUS,
            is_real_photo=True,
            is_relevant=True,
            verification_status=VerificationStatus.CONFIRMED,
            confidence=0.91,
            verification_reason="Explicit university context matches the photo.",
        )
        return UniversityProfile(
            university=University(name="Example University"),
            categories={ImageCategory.CAMPUS: [image]},
            statistics=ProfileStatistics(
                found=1, duplicates_removed=0, verified=1, rejected=0
            ),
        )


class MissingPipeline:
    async def generate(self, query: str) -> UniversityProfile:
        raise ProfileNotFoundError("missing")


def test_profile_endpoint_returns_structured_profile() -> None:
    app.dependency_overrides[get_profile_pipeline] = lambda: SuccessfulPipeline()
    try:
        response = client.post(
            "/api/profile/generate", json={"query": "Example University"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert set(body["categories"]) == {
        "campus",
        "library",
        "dormitory",
        "classroom",
        "student_life",
        "facilities",
        "other",
    }
    assert body["categories"]["campus"][0]["source_url"]
    assert body["statistics"]["verified"] == 1


def test_profile_endpoint_returns_404_for_unknown_university() -> None:
    app.dependency_overrides[get_profile_pipeline] = lambda: MissingPipeline()
    try:
        response = client.post(
            "/api/profile/generate", json={"query": "Missing University"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["status"] == "not_found"
