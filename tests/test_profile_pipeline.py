import asyncio

import pytest

from app.models.image import ImageCandidate, ImageCategory, VerificationStatus
from app.models.resolution import ResolutionStatus, UniversityResolutionResponse
from app.models.university import University
from app.services.image_sources.base import ImageSourceError
from app.services.profile_pipeline import (
    ProfileAmbiguousError,
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
    second = await pipeline.generate("Example University")

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
    assert first.preliminary_images == []
    assert first.categories[ImageCategory.CAMPUS][0].source_url is not None
    assert first.campus_summary is not None
    assert first.summary_sources == [str(first.categories[ImageCategory.CAMPUS][0].source_url)]
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
    assert "Image discovery sources were unavailable." in profile.warnings


class SlowResolver:
    async def resolve(self, query: str):
        await asyncio.sleep(0.1)


@pytest.mark.anyio
async def test_ai_failure_keeps_candidates_visible_without_fake_confirmation():
    class FailedVerifier:
        async def verify_many(self, images, university):
            raise RuntimeError("provider unavailable")

    profile = await ProfilePipelineService(
        resolver=Resolver(), discovery=Discovery(), deduplicator=Deduplicator(),
        verifier=FailedVerifier(), timeout_seconds=2,
    ).generate("Example University")
    assert len(profile.preliminary_images) == 2
    assert profile.statistics.verified == 0
    assert all(image.verification_status == "uncertain" for image in profile.preliminary_images)
    assert all(image.confidence is None for image in profile.preliminary_images)
    assert all(image.source_url for image in profile.preliminary_images)


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


@pytest.mark.anyio
async def test_resolver_timeout_is_not_overall_generation_timeout():
    from app.services.university_resolver import UniversityResolverError
    class TimedOutResolver:
        async def resolve(self, query):
            raise TimeoutError("upstream timeout")
    pipeline = ProfilePipelineService(resolver=TimedOutResolver(), discovery=Discovery(),
        deduplicator=Deduplicator(), verifier=Verifier(), timeout_seconds=2)
    with pytest.raises(UniversityResolverError):
        await pipeline.generate("Example")


@pytest.mark.anyio
async def test_resolution_can_take_longer_than_seven_seconds():
    class DelayedResolver(Resolver):
        async def resolve(self, query):
            await asyncio.sleep(7.1)
            return await super().resolve(query)
    pipeline = ProfilePipelineService(resolver=DelayedResolver(), discovery=Discovery(),
        deduplicator=Deduplicator(), verifier=Verifier(), timeout_seconds=10)
    profile = await pipeline.generate("Example")
    assert profile.statistics.verified == 2


@pytest.mark.anyio
async def test_pipeline_ranks_best_and_keeps_good_extras():
    class KeepAll:
        async def deduplicate(self, images):
            return images

    class RankedVerifier:
        async def verify_many(self, images, university):
            return [image.model_copy(update={
                "is_real_photo": True, "is_relevant": True,
                "verification_status": VerificationStatus.LIKELY,
                "confidence": .9, "quality_score": quality,
            }) for image, quality in zip(images, (.7, .95, .3))]

    profile = await ProfilePipelineService(resolver=Resolver(), discovery=Discovery(),
        deduplicator=KeepAll(), verifier=RankedVerifier(), timeout_seconds=2).generate("Example")
    campus = profile.categories[ImageCategory.CAMPUS]
    assert [i.quality_score for i in campus] == [.95, .7]
    assert [i.is_primary for i in campus] == [True, False]
    assert profile.categories[ImageCategory.LIBRARY] == []
    assert len(profile.preliminary_images) == 1


@pytest.mark.anyio
async def test_other_requires_interesting_relevant_verified_photo():
    class KeepAll:
        async def deduplicate(self, images):
            return images

    class OtherVerifier:
        async def verify_many(self, images, university):
            return [image.model_copy(update={
                "category": ImageCategory.OTHER,
                "is_real_photo": True, "is_relevant": index != 2,
                "verification_status": VerificationStatus.LIKELY if index != 2 else VerificationStatus.REJECTED,
                "confidence": .9, "quality_score": .9,
                "is_interesting": index != 1,
                "interest_reason": "Identifiable university sculpture" if index != 1 else "",
            }) for index, image in enumerate(images)]

    profile = await ProfilePipelineService(resolver=Resolver(), discovery=Discovery(),
        deduplicator=KeepAll(), verifier=OtherVerifier(), timeout_seconds=2).generate("Example")
    assert len(profile.categories[ImageCategory.OTHER]) == 1
    assert profile.categories[ImageCategory.OTHER][0].is_primary
    assert len(profile.preliminary_images) == 1
    assert profile.statistics.rejected == 1


@pytest.mark.anyio
async def test_36_candidates_return_partial_profile_within_deadline():
    import time
    from app.models.profile import PROFILE_CATEGORIES
    from app.models.verification import RankedImageDecision
    from app.services.image_verifier import ImageVerifier

    class SixPerCategory:
        async def search(self, university, limit_per_query):
            assert limit_per_query == 6
            return [_image(index * 6 + offset, category)
                    for index, category in enumerate(PROFILE_CATEGORIES[:6])
                    for offset in range(6)]

    class KeepAll:
        async def deduplicate(self, images):
            return images

    class Provider:
        def __init__(self):
            self.calls = 0
            self.cancelled = False

        async def verify_group(self, images, university):
            self.calls += 1
            assert len(images) == 6
            if images[0].category == ImageCategory.LIBRARY:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return {str(image.id): RankedImageDecision(
                image_id=str(image.id), is_real_photo=True, is_relevant=True,
                category=image.category, confidence=.9, quality_score=.8,
                verification_status="likely", reason="Source and visual context")
                for image in images}

    provider = Provider()
    started = time.monotonic()
    profile = await ProfilePipelineService(resolver=Resolver(), discovery=SixPerCategory(),
        deduplicator=KeepAll(), verifier=ImageVerifier(provider),
        timeout_seconds=.8, limit_per_query=6).generate("Example")
    assert time.monotonic() - started < .8
    assert provider.calls == 6 and provider.cancelled
    assert profile.statistics.found == 36
    assert profile.statistics.verified == 30
    assert len(profile.preliminary_images) == 6
    assert all(image.category == ImageCategory.LIBRARY for image in profile.preliminary_images)


@pytest.mark.anyio
async def test_ambiguous_university_can_be_selected_by_id():
    first = University(name="University", city="First City")
    second = University(name="University", city="Second City")

    class AmbiguousResolver:
        async def resolve(self, query):
            return UniversityResolutionResponse(
                status=ResolutionStatus.AMBIGUOUS, candidates=[first, second]
            )

    class EmptyDiscovery:
        async def search(self, university, limit_per_query):
            return []

    pipeline = ProfilePipelineService(
        resolver=AmbiguousResolver(), discovery=EmptyDiscovery(),
        deduplicator=Deduplicator(), verifier=Verifier(), timeout_seconds=2,
    )
    with pytest.raises(ProfileAmbiguousError):
        await pipeline.generate("University")

    result = await pipeline.generate("University", selected_university_id=second.id)
    assert result.university.id == second.id
    assert result.campus_summary is None
