import asyncio
import hashlib
import logging
import math
from io import BytesIO
from statistics import median

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.models.image import ImageCandidate


logger = logging.getLogger(__name__)

_PHASH_SIZE = 8
_DCT_SIZE = 32
_COSINES = tuple(
    tuple(
        math.cos((2 * position + 1) * frequency * math.pi / (2 * _DCT_SIZE))
        for position in range(_DCT_SIZE)
    )
    for frequency in range(_PHASH_SIZE)
)


class ImageDownloadTooLarge(ValueError):
    pass


class ImageDeduplicator:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        perceptual_threshold: int | None = None,
        max_download_bytes: int | None = None,
        download_timeout_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._threshold = (
            settings.perceptual_hash_threshold
            if perceptual_threshold is None
            else perceptual_threshold
        )
        if not 0 <= self._threshold <= 64:
            raise ValueError("perceptual_threshold must be between 0 and 64")
        self._max_download_bytes = (
            settings.image_download_max_bytes
            if max_download_bytes is None
            else max_download_bytes
        )
        self._download_timeout_seconds = (
            settings.image_download_timeout_seconds
            if download_timeout_seconds is None
            else download_timeout_seconds
        )

    async def deduplicate(
        self, images: list[ImageCandidate], *, timeout_seconds: float | None = None
    ) -> list[ImageCandidate]:
        logger.info("found %d images", len(images))
        semaphore = asyncio.Semaphore(12)

        async def prepare(image):
            async with semaphore:
                try:
                    return await asyncio.wait_for(self._with_hashes(image), timeout=2.5)
                except TimeoutError:
                    return image

        tasks = [asyncio.create_task(prepare(image)) for image in images]
        try:
            if tasks:
                done, _ = await asyncio.wait(tasks, timeout=timeout_seconds)
                # Preserve fast downloads and hashes even if another host stalls.
                images = [task.result() if task in done else image
                          for task, image in zip(tasks, images)]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        unique: list[ImageCandidate] = []
        seen_urls: set[str] = set()
        seen_content_hashes: set[str] = set()
        kept_perceptual_hashes: list[str] = []
        removed = 0

        for image in images:
            url = str(image.image_url)
            if url in seen_urls:
                removed += 1
                continue
            seen_urls.add(url)

            if (
                image.content_hash is not None
                and image.content_hash in seen_content_hashes
            ):
                removed += 1
                continue
            if image.perceptual_hash is not None and any(
                self.hamming_distance(image.perceptual_hash, kept_hash)
                <= self._threshold
                for kept_hash in kept_perceptual_hashes
            ):
                removed += 1
                continue

            # Already attempted above. Never redownload failed images serially.
            candidate = image
            if (
                candidate.content_hash is not None
                and candidate.content_hash in seen_content_hashes
            ):
                removed += 1
                continue

            if candidate.perceptual_hash is not None and any(
                self.hamming_distance(candidate.perceptual_hash, kept_hash)
                <= self._threshold
                for kept_hash in kept_perceptual_hashes
            ):
                removed += 1
                continue

            unique.append(candidate)
            if candidate.content_hash is not None:
                seen_content_hashes.add(candidate.content_hash)
            if candidate.perceptual_hash is not None:
                kept_perceptual_hashes.append(candidate.perceptual_hash)

        logger.info("removed %d duplicates", removed)
        logger.info("remaining %d images", len(unique))
        return unique

    async def _with_hashes(self, image: ImageCandidate) -> ImageCandidate:
        if image.content_hash is not None and image.perceptual_hash is not None:
            return image

        try:
            content = await self._download(str(image.image_url))
        except (httpx.HTTPError, ImageDownloadTooLarge) as exc:
            logger.warning(
                "Could not download image for deduplication url=%s error=%s",
                image.image_url,
                type(exc).__name__,
            )
            return image

        content_hash = hashlib.sha256(content).hexdigest()
        perceptual_hash = image.perceptual_hash
        if perceptual_hash is None:
            try:
                perceptual_hash = await asyncio.to_thread(self.compute_perceptual_hash, content)
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                logger.warning(
                    "Could not decode image for perceptual hashing url=%s error=%s",
                    image.image_url,
                    type(exc).__name__,
                )

        result = image.model_copy(
            update={
                "content_hash": content_hash,
                "perceptual_hash": perceptual_hash,
            }
        )
        result._downloaded_bytes = content
        return result

    async def _download(self, url: str) -> bytes:
        content = bytearray()
        async with self._client.stream(
            "GET", url, timeout=self._download_timeout_seconds, follow_redirects=True
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self._max_download_bytes:
                    raise ImageDownloadTooLarge(
                        f"image exceeds {self._max_download_bytes} bytes"
                    )
        return bytes(content)

    @staticmethod
    def compute_perceptual_hash(content: bytes) -> str:
        with Image.open(BytesIO(content)) as source:
            grayscale = source.convert("L").resize(
                (_DCT_SIZE, _DCT_SIZE), Image.Resampling.LANCZOS
            )
            pixels = list(grayscale.get_flattened_data())

        rows = [pixels[offset : offset + _DCT_SIZE] for offset in range(0, len(pixels), _DCT_SIZE)]
        horizontal = [
            [
                sum(row[x] * _COSINES[u][x] for x in range(_DCT_SIZE))
                for u in range(_PHASH_SIZE)
            ]
            for row in rows
        ]
        coefficients = [
            sum(
                horizontal[y][u] * _COSINES[v][y]
                for y in range(_DCT_SIZE)
            )
            for v in range(_PHASH_SIZE)
            for u in range(_PHASH_SIZE)
        ]
        threshold = median(coefficients)
        bits = 0
        for coefficient in coefficients:
            bits = (bits << 1) | int(coefficient > threshold)
        return f"{bits:016x}"

    @staticmethod
    def hamming_distance(first: str, second: str) -> int:
        return (int(first, 16) ^ int(second, 16)).bit_count()
