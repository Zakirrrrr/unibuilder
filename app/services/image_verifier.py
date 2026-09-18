import asyncio
import logging

from app.config import settings
from app.models.image import ImageCandidate, VerificationStatus
from app.models.university import University
from app.models.verification import ImageVerificationDecision
from app.services.ai_providers.base import (
    AIMalformedResponseError,
    AIProviderError,
    ImageVerificationProvider,
)


logger = logging.getLogger(__name__)


class ImageVerifier:
    def __init__(self, provider: ImageVerificationProvider) -> None:
        self._provider = provider
        self._semaphore = asyncio.Semaphore(max(1, settings.profile_ai_concurrency))

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
        self, images: list[ImageCandidate], university: University,
        *, timeout_seconds: float = 25,
    ) -> list[ImageCandidate]:
        semaphore = self._semaphore

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
                    "is_real_photo": None, "is_relevant": None, "confidence": None,
                    "verification_reason": reason,
                }
            )

        tasks = [asyncio.create_task(verify_one(image)) for image in images]
        if not tasks:
            return []
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            return [
                task.result() if task in done else image.model_copy(update={
                    "verification_status": VerificationStatus.UNCERTAIN,
                    "is_real_photo": None, "is_relevant": None, "confidence": None,
                    "verification_reason": "Verification deadline reached.",
                })
                for task, image in zip(tasks, images)
            ]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def select_many(self, images, university, *, timeout_seconds=25):
        """Concurrent category comparisons, at most six images per call."""
        if not hasattr(self._provider, "verify_group"):
            return await self.verify_many(images, university, timeout_seconds=timeout_seconds)
        groups = {}
        for image in images:
            groups.setdefault(image.category, []).append(image)

        async def compare(group):
            try:
                async with self._semaphore:
                    return await self._provider.verify_group(group, university)
            except Exception as exc:
                logger.warning("category selection unavailable error=%s", type(exc).__name__)
                return {}

        tasks = [asyncio.create_task(compare(group[start:start + 6]))
                 for group in groups.values() for start in range(0, len(group), 6)]
        decisions = {}
        try:
            if tasks:
                done, _ = await asyncio.wait(tasks, timeout=timeout_seconds)
                for task in done:
                    decisions.update(task.result())
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for image in images:
            decision = decisions.get(str(image.id))
            updates = dict(is_real_photo=None, is_relevant=None, confidence=None,
                           quality_score=None, is_primary=False, is_interesting=False, interest_reason=None,
                           verification_status=VerificationStatus.UNCERTAIN,
                           verification_reason="AI selection unavailable or deadline reached.")
            if decision:
                updates.update(decision.model_dump(exclude={"image_id", "reason"}))
                updates["verification_reason"] = decision.reason
                updates["verification_status"] = self._verification_status(decision, image, university)
            results.append(image.model_copy(update=updates))
        return results

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

        # AI evaluates affiliation; no exact title, domain or address gate.
        if decision.verification_status == "uncertain":
            return VerificationStatus.UNCERTAIN
        if decision.verification_status == "rejected":
            return VerificationStatus.UNCERTAIN
        if (
            decision.confidence >= 0.85
        ):
            if decision.verification_status == "likely":
                return VerificationStatus.LIKELY
            return VerificationStatus.CONFIRMED
        if decision.confidence >= 0.65:
            return VerificationStatus.LIKELY
        return VerificationStatus.UNCERTAIN
