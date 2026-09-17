from abc import ABC, abstractmethod

from app.models.image import ImageCandidate
from app.models.university import University


class ImageSourceError(RuntimeError):
    """Raised when an image provider cannot return a valid response."""


class ImageSource(ABC):
    @abstractmethod
    async def search(
        self, university: University, query: str, limit: int
    ) -> list[ImageCandidate]:
        """Search a provider without asserting relevance or photo authenticity."""
