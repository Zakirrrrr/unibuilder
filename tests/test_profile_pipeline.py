import asyncio

import pytest

from app.models.image import ImageCandidate, ImageCategory, VerificationStatus
from app.models.resolution import ResolutionStatus, UniversityResolutionResponse
from app.models.university import University
from app.services.image_sources.base import ImageSourceError
from app.services.profile_pipeline import (
    ProfileGenerationTimeoutError,
    ProfilePipelineService,
)


def _image(index: int, category: ImageCategory) -> ImageCandidate:
    return ImageCandidate(
        image_url=f"https://upload.wikimedia.org/{index}.jpg",
        source_url=f"https://commons.wikimedia.org/wiki/File:{index}.jpg",
        source_name="Wikimedia Commons",
        search_query=f"Example University {category.value}",
        category=category,
        title=f"Image {index}",
    )


class Resolver:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, query: str) -> UniversityResolutionResponse:
        self.calls += 1
        return UniversityResolutionResponse(
            status=ResolutionStatus.RESOLVED,
            university=University(
                name="Example University",
                aliases=["EU"],
                city="Example City",
                country="Exampleland",
                official_domain="example.edu",
            ),
        )


class Discovery:
    async def search(self, university: University, limit_per_query: int):
        return [
            _image(1, ImageCategory.CAMPUS),
            _image(2, ImageCategory.CAMPUS),
            _image(3, ImageCategory.LIBRARY),
        ]


class Deduplicator:
    async def deduplicate(self, images: list[ImageCandidate]):
        if not images:
            return []
        return [images[0], images[2]]


class Verifier:
    async def verify_many(self, images: list[ImageCandidate], university: University):
        if not images:
            return []
        return [
            images[0].model_copy(
                update={
                    "is_real_photo": True,
                    "is_relevant": True,
                    "verification_status": VerificationStatus.CONFIRMED,
                    "confidence": 0.91,
                    "verification_reason": "Official context and pixels agree.",
                }
            ),
            images[1].model_copy(
                update={
                    "is_real_photo": True,
                    "is_relevant": False,
                    "verification_status": VerificationStatus.REJECTED,
                    "confidence": 0.98,
                    "verification_reason": "The metadata identifies another place.",
                }
            ),
        ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_pipeline_builds_profile_filters_rejected_and_caches() -> None:
    resolver = Resolver()
    pipeline = ProfilePipelineService(
        resolver=resolver,
        discovery=Discovery(),
        deduplicator=Deduplicator(),
        verifier=Verifier(),
        timeout_seconds=2,
        cache_ttl_seconds=60,
        limit_per_query=2,
    )

    first = await pipeline.generate("Example University")
    second = await pipeline.generate("EU")

    assert resolver.calls == 1
    assert first.generated_at == second.generated_at
    assert first.statistics.model_dump() == {
        "found": 3,
        "duplicates_removed": 1,
        "verified": 2,
        "rejected": 1,
    }
    assert len(first.categories[ImageCategory.CAMPUS]) == 1
    assert first.categories[ImageCategory.LIBRARY] == []
    assert first.categories[ImageCategory.CAMPUS][0].source_url is not None
    assert all(
        image.verification_status != VerificationStatus.REJECTED
        for images in first.categories.values()
        for image in images
    )


class FailingDiscovery:
    async def search(self, university: University, limit_per_query: int):
        raise ImageSourceError("unavailable")


@pytest.mark.anyio
async def test_pipeline_returns_partial_profile_when_discovery_fails() -> None:
    profile = await ProfilePipelineService(
        resolver=Resolver(),
        discovery=FailingDiscovery(),
        deduplicator=Deduplicator(),
        verifier=Verifier(),
        timeout_seconds=2,
        cache_ttl_seconds=0,
    ).generate("Example University")

    assert profile.statistics.found == 0
    assert profile.statistics.verified == 0
    assert profile.warnings == ["Image discovery sources were unavailable."]


class SlowResolver:
    async def resolve(self, query: str):
        await asyncio.sleep(0.1)


@pytest.mark.anyio
async def test_pipeline_enforces_overall_timeout() -> None:
    pipeline = ProfilePipelineService(
        resolver=SlowResolver(),
        discovery=Discovery(),
        deduplicator=Deduplicator(),
        verifier=Verifier(),
        timeout_seconds=0.01,
        cache_ttl_seconds=0,
    )

    with pytest.raises(ProfileGenerationTimeoutError):
        await pipeline.generate("Example University")
