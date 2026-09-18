"""Google Images results via SerpAPI; never invent publisher metadata."""
import asyncio
import logging
import re

import httpx
from pydantic import ValidationError

from app.models.image import ImageCandidate, ImageCategory
from app.models.university import University
from app.services.image_sources.base import ImageSource, ImageSourceError


TOPICS = dict(zip(
    ("campus", "library", "dormitory", "students", "classroom", "building"),
    (ImageCategory.CAMPUS, ImageCategory.LIBRARY, ImageCategory.DORMITORY,
     ImageCategory.STUDENT_LIFE, ImageCategory.CLASSROOM, ImageCategory.FACILITIES),
))


class _RedactSearchKey(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r"([?&]api_key=)[^&\s\"]+", r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactSearchKey())


class GoogleImagesSource(ImageSource):
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client, self._key = client, api_key

    async def search(self, university: University, query: str, limit: int) -> list[ImageCandidate]:
        if not self._key:
            raise ImageSourceError("SERPAPI_API_KEY is not configured")
        for attempt in range(2):
            try:
                response = await self._client.get("https://serpapi.com/search.json", params={
                    "engine": "google_images", "q": query, "api_key": self._key,
                    "safe": "active", "ijn": 0,
                }, timeout=7)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise ValueError("Search provider error")
                rows = data.get("images_results", [])
                if not isinstance(rows, list):
                    raise ValueError("Invalid search results")
                images, seen = [], set()
                for row in rows:
                    if not isinstance(row, dict) or row.get("unsafe") or row.get("is_product"):
                        continue
                    try:
                        image = ImageCandidate(
                            image_url=row.get("original"), source_url=row.get("link"),
                            source_name="Google Images / " + (row.get("source") or "Unknown publisher"),
                            search_query=query, title=row.get("title"),
                            category=TOPICS.get(query.rsplit(" ", 1)[-1], ImageCategory.OTHER),
                        )
                    except (ValidationError, TypeError):
                        continue
                    if str(image.image_url) in seen:
                        continue
                    seen.add(str(image.image_url))
                    images.append(image)
                    if len(images) >= min(6, max(1, limit)):
                        break
                return images
            except (httpx.HTTPError, ValueError):
                if attempt == 0:
                    await asyncio.sleep(.15)
        # Do not include request URLs: SerpAPI authenticates in the query string.
        raise ImageSourceError("Google Images search unavailable") from None
