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
from app.services.university_resolver import UniversityResolver, UniversityResolverError
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
        self._timeout = min(29.0, (
            settings.profile_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        ))
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
        if not 1 <= self._limit_per_query <= 6:
            raise ValueError("limit_per_query must be between 1 and 6")
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
        timeout = asyncio.timeout(self._timeout)
        try:
            async with timeout:
                return await self._generate_uncached(query)
        except TimeoutError as exc:
            if not timeout.expired():
                raise
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
        deadline = started_at + self._timeout
        # Resolution shares the overall deadline; no hidden seven-second cutoff.
        try:
            resolution = await self._resolver.resolve(query)
        except TimeoutError as exc:
            raise UniversityResolverError("University resolution request timed out") from exc
        if resolution.status == ResolutionStatus.NOT_FOUND:
            raise ProfileNotFoundError("University was not found")
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            raise ProfileAmbiguousError(resolution.candidates)
        university = resolution.university
        if university is None:  # Protected by the response model; keep boundary safe.
            raise ProfileNotFoundError("University resolution returned no entity")

        warnings: list[str] = []
        if not settings.serpapi_api_key:
            warnings.append("Google Images не подключён: добавьте SERPAPI_API_KEY в .env. Используются резервные источники.")
        try:
            found_images = await asyncio.wait_for(
                self._discovery.search(university, min(6, max(5, self._limit_per_query))),
                timeout=max(.01, deadline - time.monotonic() - 1),
            )
        except (ImageSourceError, TimeoutError):
            logger.exception("all image sources failed university=%s", university.name)
            warnings.append("Image discovery sources were unavailable.")
            found_images = []
        found_count = len(found_images)
        # Prioritize explicit campus context and official provenance; bound AI cost.
        found_images.sort(key=lambda image: (
            image.source_name != "Official website",
            -sum(word in ((image.title or "") + " " + (image.description or "")).lower()
                 for word in ("campus", "library", "yard", "hall", "building")),
        ))
        selected_images = []
        for category in PROFILE_CATEGORIES:
            selected_images.extend([image for image in found_images if image.category == category][:6])
        if len(found_images) > len(selected_images):
            warnings.append(f"Checking the top {len(selected_images)} of {found_count} candidates within 30 seconds.")

        try:
            dedup_budget = max(.01, min(3.5, deadline - time.monotonic() - 1))
            if isinstance(self._deduplicator, ImageDeduplicator):
                unique_images = await self._deduplicator.deduplicate(
                    selected_images, timeout_seconds=dedup_budget)
            else:
                unique_images = await asyncio.wait_for(
                    self._deduplicator.deduplicate(selected_images), dedup_budget)
        except Exception as exc:
            logger.exception(
                "deduplication stage failed university=%s error=%s",
                university.name,
                type(exc).__name__,
            )
            warnings.append("Deduplication was only partially available.")
            unique_images = selected_images

        try:
            if isinstance(self._verifier, ImageVerifier):
                verified_images = await self._verifier.select_many(
                    unique_images, university,
                    timeout_seconds=max(.01, deadline - time.monotonic() - .5),
                )
            else:
                verified_images = await self._verifier.verify_many(unique_images, university)
        except Exception as exc:
            logger.exception(
                "verification stage failed university=%s error=%s",
                university.name,
                type(exc).__name__,
            )
            warnings.append("AI verification was only partially available.")
            verified_images = [self._uncertain(image) for image in unique_images]

        categories = empty_profile_categories()
        preliminary_images = []
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
                image._downloaded_bytes = None
                preliminary_images.append(image)
                continue
            if (image.category in PROFILE_CATEGORIES
                and image.is_real_photo and image.is_relevant
                and image.verification_status in (VerificationStatus.CONFIRMED, VerificationStatus.LIKELY)
                and (image.category != "other" or (image.is_interesting and image.interest_reason))
                and (image.quality_score is None or image.quality_score >= .6)):
                image._downloaded_bytes = None
                categories[image.category].append(image)
            else:
                image._downloaded_bytes = None
                preliminary_images.append(image)

        # Search candidates beyond the AI budget remain visible with honest status.
        visible_urls = {str(image.image_url) for image in verified_images}
        selected_ids = {image.id for image in selected_images}
        for image in found_images:
            if image.id in selected_ids:
                continue
            if str(image.image_url) not in visible_urls:
                preliminary_images.append(self._uncertain(image))
                visible_urls.add(str(image.image_url))

        for group in categories.values():
            group.sort(key=lambda image: (image.quality_score or 0, image.confidence or 0), reverse=True)
            for index, image in enumerate(group):
                image.is_primary = index == 0

        unavailable_count = len(verified_images) - verified_count
        if unavailable_count:
            warnings.append(
                f"Для {unavailable_count} изображений проверка недоступна: источник, AI или таймаут. Они сохранены отдельно как предварительные."
            )

        profile = UniversityProfile(
            university=university,
            preliminary_images=preliminary_images,
            categories=categories,
            statistics=ProfileStatistics(
                found=found_count,
                duplicates_removed=len(selected_images) - len(unique_images),
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
        # Google photo URLs are temporary; do not persist them in the profile cache.
        all_images = profile.preliminary_images + [i for group in profile.categories.values() for i in group]
        if any(image.source_name == "Google Maps" for image in all_images):
            return
        if self._cache_ttl == 0:
            return
        entry = _CachedProfile(
            profile=profile.model_copy(deep=True),
            expires_at=time.monotonic() + (min(self._cache_ttl, 15) if profile.warnings else self._cache_ttl),
        )
        keys = {
            query_key,
        }
        for key in keys:
            if key:
                self._cache[key] = entry

    @staticmethod
    def _uncertain(image: ImageCandidate) -> ImageCandidate:
        return image.model_copy(
            update={
                "verification_status": VerificationStatus.UNCERTAIN,
                "is_real_photo": None, "is_relevant": None, "confidence": None,
                "verification_reason": "Найдено в источнике. AI-проверка не завершена; принадлежность университету не подтверждена.",
            }
        )
