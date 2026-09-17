import asyncio
import logging
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import settings
from app.models.image import ImageCandidate, ImageCategory
from app.models.university import University
from app.services.image_sources.base import ImageSource, ImageSourceError
from app.utils.text import normalize_query


logger = logging.getLogger(__name__)

_CATEGORY_KEYWORDS = (
    ("library", ImageCategory.LIBRARY),
    ("dormitory", ImageCategory.DORMITORY),
    ("classroom", ImageCategory.CLASSROOM),
    ("students", ImageCategory.STUDENT_LIFE),
    ("campus", ImageCategory.CAMPUS),
    ("building", ImageCategory.FACILITIES),
)
_UNKNOWN_VALUES = {"", "unknown", "unknown author", "n/a", "not provided"}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return normalize_query(unescape(" ".join(self.parts)))


class WikimediaSource(ImageSource):
    source_name = "Wikimedia Commons"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def search(
        self, university: University, query: str, limit: int
    ) -> list[ImageCandidate]:
        del university  # The orchestrator has already incorporated it into the query.
        try:
            payload = await self._request(
                {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": "6",
                    "gsrlimit": str(limit),
                    "gsrsort": "relevance",
                    "prop": "imageinfo",
                    "iiprop": "url|mime|mediatype|extmetadata",
                    "iiextmetadatalanguage": "en",
                    "iiextmetadatafilter": (
                        "ImageDescription|Artist|LicenseShortName|UsageTerms"
                    ),
                }
            )
            return self._parse_images(payload, query)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Wikimedia Commons search failed: %s", type(exc).__name__)
            raise ImageSourceError("Wikimedia Commons is temporarily unavailable") from exc

    async def _request(self, params: dict[str, str]) -> dict[str, Any]:
        request_params = {
            "format": "json",
            "formatversion": "2",
            "maxlag": str(settings.wikimedia_maxlag_seconds),
            **params,
        }
        for attempt in range(settings.wikimedia_retry_attempts):
            response = await self._client.get(
                settings.wikimedia_commons_api_url,
                params=request_params,
                timeout=settings.wikimedia_timeout_seconds,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < settings.wikimedia_retry_attempts - 1:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Wikimedia response must be an object")
            error = payload.get("error")
            if not isinstance(error, dict):
                return payload
            logger.warning(
                "Wikimedia API error code=%s info=%s",
                error.get("code", "unknown"),
                error.get("info", "not provided"),
            )
            if (
                error.get("code") == "maxlag"
                and attempt < settings.wikimedia_retry_attempts - 1
            ):
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            raise ValueError("Wikimedia returned an API error")
        raise ValueError("Wikimedia retry limit exceeded")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 5.0)
        return 1.0 * (attempt + 1)

    def _parse_images(
        self, payload: dict[str, Any], query: str
    ) -> list[ImageCandidate]:
        pages = payload.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            raise ValueError("Wikimedia pages must be a list")

        images: list[ImageCandidate] = []
        for page in pages:
            image_info_list = page.get("imageinfo", [])
            if not image_info_list:
                continue
            image_info = image_info_list[0]
            image_url = self._canonical_image_url(image_info.get("url"))
            source_url = image_info.get("descriptionurl")
            if not image_url or not source_url:
                continue
            if image_info.get("mediatype") not in {None, "BITMAP"}:
                continue
            mime = image_info.get("mime")
            if mime and not str(mime).startswith("image/"):
                continue

            metadata = image_info.get("extmetadata", {})
            images.append(
                ImageCandidate(
                    image_url=image_url,
                    source_url=source_url,
                    source_name=self.source_name,
                    search_query=query,
                    title=self._nullable_text(page.get("title")),
                    description=self._metadata(metadata, "ImageDescription"),
                    author=self._metadata(metadata, "Artist"),
                    license=(
                        self._metadata(metadata, "LicenseShortName")
                        or self._metadata(metadata, "UsageTerms")
                    ),
                    category=self._category(query),
                    is_real_photo=None,
                    is_relevant=None,
                )
            )
        return images

    @staticmethod
    def _metadata(metadata: dict[str, Any], key: str) -> str | None:
        entry = metadata.get(key, {})
        value = entry.get("value") if isinstance(entry, dict) else None
        return WikimediaSource._nullable_text(value)

    @staticmethod
    def _nullable_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        parser = _HTMLTextExtractor()
        parser.feed(value)
        cleaned = parser.text()
        if cleaned.casefold() in _UNKNOWN_VALUES:
            return None
        return cleaned or None

    @staticmethod
    def _category(query: str) -> ImageCategory:
        query_key = query.casefold()
        for keyword, category in _CATEGORY_KEYWORDS:
            if keyword in query_key:
                return category
        return ImageCategory.OTHER

    @staticmethod
    def _canonical_image_url(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        parsed = urlsplit(value)
        if parsed.hostname == "upload.wikimedia.org":
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return value
