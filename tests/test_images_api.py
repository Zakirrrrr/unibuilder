from fastapi.testclient import TestClient

from app.api.images import get_image_discovery_service
from app.main import app
from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_sources.base import ImageSourceError


client = TestClient(app)


class SuccessfulDiscovery:
    async def search(
        self, university: University, limit_per_query: int
    ) -> list[ImageCandidate]:
        return [
            ImageCandidate(
                image_url="https://upload.wikimedia.org/campus.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Campus.jpg",
                source_name="Wikimedia Commons",
                search_query=f'"{university.name}" campus',
            )
        ]


class FailingDiscovery:
    async def search(
        self, university: University, limit_per_query: int
    ) -> list[ImageCandidate]:
        raise ImageSourceError("unavailable")


def test_images_search_endpoint() -> None:
    app.dependency_overrides[get_image_discovery_service] = lambda: SuccessfulDiscovery()
    try:
        response = client.post(
            "/api/images/search",
            json={
                "university": {
                    "name": "Nazarbayev University",
                    "city": "Astana",
                    "country": "Kazakhstan",
                    "official_domain": "nu.edu.kz",
                },
                "limit_per_query": 5,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["images"][0]["source_url"].startswith(
        "https://commons.wikimedia.org/"
    )


def test_images_search_endpoint_rejects_excessive_limit() -> None:
    app.dependency_overrides[get_image_discovery_service] = lambda: SuccessfulDiscovery()
    try:
        response = client.post(
            "/api/images/search",
            json={
                "university": {"name": "Example University"},
                "limit_per_query": 7,
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_images_search_endpoint_returns_503() -> None:
    app.dependency_overrides[get_image_discovery_service] = lambda: FailingDiscovery()
    try:
        response = client.post(
            "/api/images/search",
            json={"university": {"name": "Example University"}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
