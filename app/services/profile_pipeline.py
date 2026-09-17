import asyncio
import logging
import time
from dataclasses import dataclass

from app.config import settings
from app.models.image import ImageCandidate, VerificationStatus
from app.models.profile import (
    PROFILE_CATEGORIES,
    ProfileStatistics,
    UniversityProfile,
    empty_profile_categories,
)
from app.models.resolution import ResolutionStatus
from app.models.university import University
from app.services.image_deduplicator import ImageDeduplicator
from app.services.image_discovery import ImageDiscoveryService
from app.services.image_sources.base import ImageSourceError
from app.services.image_verifier import ImageVerifier
from app.services.university_resolver import UniversityResolver
from app.utils.text import comparison_key, normalize_query


logger = logging.getLogger(__name__)


class ProfileNotFoundError(ValueError):
    """Raised when no university can be resolved for the query."""


class ProfileAmbiguousError(ValueError):
    def __init__(self, candidates: list[University]) -> None:
        super().__init__("University query is ambiguous")
        self.candidates = candidates


class ProfileGenerationTimeoutError(TimeoutError):
    """Raised when the complete profile pipeline exceeds its deadline."""


@dataclass(frozen=True, slots=True)
class _CachedProfile:
    profile: UniversityProfile
    expires_at: float


class ProfilePipelineService:
    def __init__(
        self,
        *,
        resolver: UniversityResolver,
        discovery: ImageDiscoveryService,
        deduplicator: ImageDeduplicator,
        verifier: ImageVerifier,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        limit_per_query: int | None = None,
    ) -> None:
        self._resolver = resolver
        self._discovery = discovery
        self._deduplicator = deduplicator
        self._verifier = verifier
        self._timeout = (
            settings.profile_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        self._cache_ttl = (
            settings.profile_cache_ttl_seconds
            if cache_ttl_seconds is None
            else cache_ttl_seconds
        )
        self._limit_per_query = (
            settings.profile_limit_per_query
            if limit_per_query is None
            else limit_per_query
        )
        if self._timeout <= 0 or self._cache_ttl < 0:
            raise ValueError("profile timeout must be positive and cache TTL non-negative")
        if not 1 <= self._limit_per_query <= 5:
            raise ValueError("limit_per_query must be between 1 and 5")
        self._cache: dict[str, _CachedProfile] = {}
        self._inflight: dict[str, asyncio.Task[UniversityProfile]] = {}
        self._cache_lock = asyncio.Lock()

    async def generate(self, raw_query: str) -> UniversityProfile:
        query = normalize_query(raw_query)
        key = comparison_key(query)
        if not key:
            raise ProfileNotFoundError("University query is empty")

        async with self._cache_lock:
            cached = self._get_cached(key)
            if cached is not None:
                logger.info("profile cache hit query=%s", query)
                return cached.model_copy(deep=True)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._generate_with_timeout(query))
                self._inflight[key] = task
                logger.info("profile generation started query=%s", query)
            else:
                logger.info("joining in-flight profile generation query=%s", query)

        try:
            profile = await asyncio.shield(task)
        finally:
            if task.done():
                async with self._cache_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

        async with self._cache_lock:
            self._store_cached(key, profile)
        return profile.model_copy(deep=True)

    async def _generate_with_timeout(self, query: str) -> UniversityProfile:
        try:
            return await asyncio.wait_for(
                self._generate_uncached(query), timeout=self._timeout
            )
        except TimeoutError as exc:
            logger.error(
                "profile generation timed out query=%s timeout=%.1fs",
                query,
                self._timeout,
            )
            raise ProfileGenerationTimeoutError(
                "Profile generation exceeded its time limit"
            ) from exc

    async def _generate_uncached(self, query: str) -> UniversityProfile:
        started_at = time.monotonic()
        resolution = await self._resolver.resolve(query)
        if resolution.status == ResolutionStatus.NOT_FOUND:
            raise ProfileNotFoundError("University was not found")
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            raise ProfileAmbiguousError(resolution.candidates)
        university = resolution.university
        if university is None:  # Protected by the response model; keep boundary safe.
            raise ProfileNotFoundError("University resolution returned no entity")

        warnings: list[str] = []
        try:
            found_images = await self._discovery.search(
                university, self._limit_per_query
            )
        except ImageSourceError:
            logger.exception("all image sources failed university=%s", university.name)
            warnings.append("Image discovery sources were unavailable.")
            found_images = []
        found_count = len(found_images)

        try:
            unique_images = await self._deduplicator.deduplicate(found_images)
        except Exception as exc:
            logger.exception(
                "deduplication stage failed university=%s error=%s",
                university.name,
                type(exc).__name__,
            )
            warnings.append("Deduplication was only partially available.")
            unique_images = found_images

        try:
            verified_images = await self._verifier.verify_many(
                unique_images, university
            )
        except Exception as exc:
            logger.exception(
                "verification stage failed university=%s error=%s",
                university.name,
                type(exc).__name__,
            )
            warnings.append("AI verification was only partially available.")
            verified_images = [self._uncertain(image) for image in unique_images]

        categories = empty_profile_categories()
        rejected_count = 0
        verified_count = 0
        for image in verified_images:
            if (
                image.is_real_photo is not None
                and image.is_relevant is not None
                and image.confidence is not None
            ):
                verified_count += 1
            if image.verification_status == VerificationStatus.REJECTED:
                rejected_count += 1
                continue
            if (
                image.is_real_photo is None
                or image.is_relevant is None
                or image.confidence is None
            ):
                continue
            if image.category in PROFILE_CATEGORIES:
                categories[image.category].append(image)

        unavailable_count = len(verified_images) - verified_count
        if unavailable_count:
            warnings.append(
                f"AI verification was unavailable for {unavailable_count} image(s)."
            )

        profile = UniversityProfile(
            university=university,
            categories=categories,
            statistics=ProfileStatistics(
                found=found_count,
                duplicates_removed=found_count - len(unique_images),
                verified=verified_count,
                rejected=rejected_count,
            ),
            warnings=warnings,
        )
        logger.info(
            "profile generation completed university=%s found=%d duplicates=%d "
            "verified=%d rejected=%d duration=%.2fs",
            university.name,
            profile.statistics.found,
            profile.statistics.duplicates_removed,
            profile.statistics.verified,
            profile.statistics.rejected,
            time.monotonic() - started_at,
        )
        return profile

    def _get_cached(self, key: str) -> UniversityProfile | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return None
        return entry.profile

    def _store_cached(self, query_key: str, profile: UniversityProfile) -> None:
        if self._cache_ttl == 0:
            return
        entry = _CachedProfile(
            profile=profile.model_copy(deep=True),
            expires_at=time.monotonic() + self._cache_ttl,
        )
        keys = {
            query_key,
            comparison_key(profile.university.name),
            *(comparison_key(alias) for alias in profile.university.aliases),
        }
        for key in keys:
            if key:
                self._cache[key] = entry

    @staticmethod
    def _uncertain(image: ImageCandidate) -> ImageCandidate:
        return image.model_copy(
            update={
                "verification_status": VerificationStatus.UNCERTAIN,
                "verification_reason": "AI verification was unavailable for this image.",
            }
        )
