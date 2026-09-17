import asyncio
import logging
from dataclasses import dataclass

from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_sources.base import ImageSource, ImageSourceError
from app.utils.text import normalize_query


@dataclass(frozen=True, slots=True)
class SearchTopic:
    term: str


SEARCH_TOPICS = (
    SearchTopic("campus"),
    SearchTopic("library"),
    SearchTopic("dormitory"),
    SearchTopic("students"),
    SearchTopic("classroom"),
    SearchTopic("building"),
)

logger = logging.getLogger(__name__)


class ImageDiscoveryService:
    def __init__(self, sources: list[ImageSource]) -> None:
        self._sources = sources

    async def search(
        self, university: University, limit_per_query: int
    ) -> list[ImageCandidate]:
        requests = [
            source.search(university, query, limit_per_query)
            for query in self.build_queries(university)
            for source in self._sources
        ]
        if not requests:
            return []

        results = await asyncio.gather(*requests, return_exceptions=True)
        failures = 0
        images: list[ImageCandidate] = []
        seen_urls: set[str] = set()
        for result in results:
            if isinstance(result, BaseException):
                failures += 1
                logger.warning(
                    "image discovery request failed error=%s",
                    type(result).__name__,
                )
                continue
            for candidate in result:
                url_key = str(candidate.image_url)
                if url_key not in seen_urls:
                    seen_urls.add(url_key)
                    images.append(candidate)

        if failures == len(results):
            raise ImageSourceError("All image discovery requests failed")
        if failures:
            logger.warning(
                "image discovery completed partially failures=%d total=%d",
                failures,
                len(results),
            )
        return images

    @staticmethod
    def build_queries(university: University) -> list[str]:
        name = normalize_query(university.name).replace('"', "")
        city = (
            normalize_query(university.city).replace('"', "")
            if university.city
            else None
        )
        return [
            " ".join(
                part
                for part in (f'"{name}"', topic.term, f'"{city}"' if city else None)
                if part
            )
            for topic in SEARCH_TOPICS
        ]
