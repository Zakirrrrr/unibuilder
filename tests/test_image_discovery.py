import pytest

from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_discovery import ImageDiscoveryService
from app.services.image_sources.base import ImageSource, ImageSourceError


class RecordingSource(ImageSource):
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    async def search(
        self, university: University, query: str, limit: int
    ) -> list[ImageCandidate]:
        self.queries.append((query, limit))
        return [
            ImageCandidate(
                image_url="https://upload.wikimedia.org/shared.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Shared.jpg",
                source_name="Wikimedia Commons",
                search_query=query,
            )
        ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_discovery_builds_six_queries_with_city_and_deduplicates() -> None:
    source = RecordingSource()
    service = ImageDiscoveryService([source])
    university = University(name="Nazarbayev University", city="Astana")

    images = await service.search(university, limit_per_query=5)

    assert len(source.queries) == 9
    assert all('"Nazarbayev University"' in query for query, _ in source.queries[:-1])
    assert source.queries[-1][0] == '"Astana" city'
    assert all(limit == 5 for _, limit in source.queries)
    assert len(images) == 1


class PartiallyFailingSource(RecordingSource):
    async def search(
        self, university: University, query: str, limit: int
    ) -> list[ImageCandidate]:
        if "library" in query:
            raise ImageSourceError("one query failed")
        return await super().search(university, query, limit)


@pytest.mark.anyio
async def test_discovery_preserves_partial_results() -> None:
    service = ImageDiscoveryService([PartiallyFailingSource()])

    images = await service.search(
        University(name="Example University"), limit_per_query=2
    )

    assert len(images) == 1
