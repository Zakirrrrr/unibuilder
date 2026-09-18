import httpx

from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_sources.base import ImageSource


class OpenverseSource(ImageSource):
    """One university-wide query; preserves the original publisher's landing URL."""
    university_wide = True

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def search(self, university: University, query: str, limit: int):
        response = await self._client.get(
            "https://api.openverse.org/v1/images/",
            params={"q": f'"{university.name}" campus', "page_size": min(limit * 3, 12)},
            timeout=4,
        )
        response.raise_for_status()
        images = []
        for item in response.json().get("results", []):
            if not item.get("url") or not item.get("foreign_landing_url"):
                continue
            try:
                images.append(ImageCandidate(
                    image_url=item["url"], source_url=item["foreign_landing_url"],
                    source_name=f"Openverse / {item.get('source') or 'publisher'}",
                    search_query=query, title=item.get("title") or None,
                    author=item.get("creator") or None,
                    license=item.get("license") or None,
                ))
            except ValueError:
                continue
        return images
