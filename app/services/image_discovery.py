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
    SearchTopic("sport"),
    SearchTopic("laboratory"),
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
            for source in self._sources
            for query in (
                [university.name] if getattr(source, "university_wide", False)
                else self.build_queries(university)
            )
        ]
        if not requests:
            return []

        tasks = [asyncio.create_task(request) for request in requests]
        try:
            done, pending = await asyncio.wait(tasks, timeout=8.0)
            results = []
            for task in tasks:
                if task in done:
                    try:
                        results.append(task.result())
                    except Exception as exc:
                        results.append(exc)
                else:
                    results.append(TimeoutError("Image source deadline"))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
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
        # Requiring city AND topic excludes many correctly identified campus files.
        queries = [
            " ".join(
                part
                for part in (f'"{name}"', topic.term)
                if part
            )
            for topic in SEARCH_TOPICS
        ]
        if university.city:
            city = normalize_query(university.city).replace('"', "")
            queries.append(f'"{city}" city')
        return queries
