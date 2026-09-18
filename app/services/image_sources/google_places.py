import asyncio
import re

import httpx

from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_sources.base import ImageSource


class GooglePlacesSource(ImageSource):
    """User/owner photos attached to place candidates, not automatically verified."""
    university_wide = True

    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._headers = {"X-Goog-Api-Key": api_key}

    async def search(self, university: University, query: str, limit: int):
        response = await self._client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={**self._headers, "X-Goog-FieldMask":
                     "places.displayName,places.formattedAddress,places.googleMapsUri,places.photos"},
            json={"textQuery": " ".join(filter(None, [university.name, university.city])),
                  "pageSize": 2}, timeout=3,
        )
        response.raise_for_status()

        async def photo_candidate(place, photo):
            name = photo.get("name", "")
            if not re.fullmatch(r"places/[^/]+/photos/[^/]+", name):
                return None
            media = await self._client.get(
                f"https://places.googleapis.com/v1/{name}/media",
                headers=self._headers,
                params={"maxWidthPx": 800, "skipHttpRedirect": "true"}, timeout=3,
            )
            media.raise_for_status()
            uri = media.json().get("photoUri")
            source = photo.get("googleMapsUri") or place.get("googleMapsUri")
            if not uri or not source:
                return None
            return ImageCandidate(
                image_url=uri, source_url=source, source_name="Google Maps",
                search_query=query, title=place.get("displayName", {}).get("text"),
                description=place.get("formattedAddress"),
                author="; ".join(a["displayName"] for a in photo.get("authorAttributions", [])
                                 if a.get("displayName")) or None,
                license=None,
            )

        tasks = [photo_candidate(place, photo)
                 for place in response.json().get("places", [])[:2]
                 for photo in place.get("photos", [])[:min(limit, 3)]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [item for item in results if isinstance(item, ImageCandidate)]
