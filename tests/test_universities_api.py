from fastapi.testclient import TestClient

from app.api.universities import get_university_resolver
from app.main import app
from app.models import ResolutionStatus, UniversityResolutionResponse
from app.services.university_resolver import UniversityResolverError


client = TestClient(app)


class NotFoundResolver:
    async def resolve(self, query: str) -> UniversityResolutionResponse:
        return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)


class FailingResolver:
    async def resolve(self, query: str) -> UniversityResolutionResponse:
        raise UniversityResolverError("upstream unavailable")


def test_resolve_endpoint() -> None:
    app.dependency_overrides[get_university_resolver] = lambda: NotFoundResolver()
    try:
        response = client.post(
            "/api/universities/resolve", json={"query": "Missing University"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_found",
        "university": None,
        "candidates": [],
    }


def test_resolve_endpoint_returns_503_for_upstream_failure() -> None:
    app.dependency_overrides[get_university_resolver] = lambda: FailingResolver()
    try:
        response = client.post("/api/universities/resolve", json={"query": "MIT"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "detail": "University data provider is temporarily unavailable"
    }
