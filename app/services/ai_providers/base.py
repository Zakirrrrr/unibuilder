from abc import ABC, abstractmethod

from app.models.image import ImageCandidate
from app.models.university import University
from app.models.verification import ImageVerificationDecision


class AIProviderError(RuntimeError):
    """Raised when an AI provider cannot return a valid decision."""


class AIProviderConfigurationError(AIProviderError):
    """Raised when an AI provider is not configured."""


class AIMalformedResponseError(AIProviderError):
    """Raised when an AI provider exhausts retries with malformed output."""


class ImageVerificationProvider(ABC):
    @abstractmethod
    async def verify(
        self, image: ImageCandidate, university: University
    ) -> ImageVerificationDecision:
        """Return a structured verification decision for one image."""

    def close(self) -> None:
        """Release provider-owned resources, if any."""
