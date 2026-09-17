import asyncio
import logging
from urllib.parse import urlparse

from app.config import settings
from app.models.image import ImageCandidate, VerificationStatus
from app.models.university import University
from app.models.verification import ImageVerificationDecision
from app.services.ai_providers.base import (
    AIMalformedResponseError,
    AIProviderError,
    ImageVerificationProvider,
)
from app.utils.text import comparison_key


logger = logging.getLogger(__name__)


class ImageVerifier:
    def __init__(self, provider: ImageVerificationProvider) -> None:
        self._provider = provider

    async def verify(
        self, image: ImageCandidate, university: University
    ) -> ImageCandidate:
        decision = await self._provider.verify(image, university)
        status = self._verification_status(decision, image, university)
        logger.info(
            "verified image id=%s status=%s confidence=%.2f",
            image.id,
            status,
            decision.confidence,
        )
        return image.model_copy(
            update={
                "is_real_photo": decision.is_real_photo,
                "is_relevant": decision.is_relevant,
                "category": decision.category,
                "confidence": decision.confidence,
                "verification_status": status,
                "verification_reason": decision.reason,
            }
        )

    async def verify_many(
        self, images: list[ImageCandidate], university: University
    ) -> list[ImageCandidate]:
        semaphore = asyncio.Semaphore(settings.profile_ai_concurrency)

        async def verify_one(image: ImageCandidate) -> ImageCandidate:
            try:
                async with semaphore:
                    return await self.verify(image, university)
            except asyncio.CancelledError:
                raise
            except AIMalformedResponseError:
                logger.warning(
                    "invalid structured AI response; marking image id=%s uncertain",
                    image.id,
                )
                reason = "AI provider did not return valid structured JSON."
            except AIProviderError as exc:
                logger.warning(
                    "AI verification unavailable for image id=%s error=%s",
                    image.id,
                    type(exc).__name__,
                )
                reason = "AI verification was unavailable for this image."
            except Exception as exc:
                logger.exception(
                    "AI verification failed for image id=%s error=%s",
                    image.id,
                    type(exc).__name__,
                )
                reason = "AI verification was unavailable for this image."
            return image.model_copy(
                update={
                    "verification_status": VerificationStatus.UNCERTAIN,
                    "verification_reason": reason,
                }
            )

        return list(await asyncio.gather(*(verify_one(image) for image in images)))

    @classmethod
    def _verification_status(
        cls,
        decision: ImageVerificationDecision,
        image: ImageCandidate,
        university: University,
    ) -> VerificationStatus:
        if not decision.is_real_photo or not decision.is_relevant:
            if decision.confidence >= 0.75:
                return VerificationStatus.REJECTED
            return VerificationStatus.UNCERTAIN

        # Provider status may make a locally supported result more conservative,
        # but it can never promote weak context to confirmed.
        if decision.verification_status == "uncertain":
            return VerificationStatus.UNCERTAIN
        if decision.verification_status == "rejected":
            return VerificationStatus.UNCERTAIN
        if (
            decision.confidence >= 0.85
            and cls._has_strong_context(image, university)
        ):
            if decision.verification_status == "likely":
                return VerificationStatus.LIKELY
            return VerificationStatus.CONFIRMED
        if decision.confidence >= 0.65:
            return VerificationStatus.LIKELY
        return VerificationStatus.UNCERTAIN

    @staticmethod
    def _has_strong_context(
        image: ImageCandidate, university: University
    ) -> bool:
        context = " ".join(
            value for value in (image.title, image.description) if value
        )
        context_key = comparison_key(context)
        names = [university.name, *university.aliases]
        if any(
            len(comparison_key(name)) >= 3
            and comparison_key(name) in context_key
            for name in names
        ):
            return True
        if university.official_domain:
            source_host = (
                urlparse(str(image.source_url)).hostname or ""
            ).casefold().removeprefix("www.")
            official_domain = university.official_domain.casefold().removeprefix(
                "www."
            )
            return source_host == official_domain or source_host.endswith(
                f".{official_domain}"
            )
        return False
