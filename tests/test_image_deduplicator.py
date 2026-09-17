import logging
from io import BytesIO

import httpx
import pytest
from PIL import Image, ImageDraw

from app.models.image import ImageCandidate
from app.services.image_deduplicator import ImageDeduplicator


def _candidate(name: str) -> ImageCandidate:
    return ImageCandidate(
        image_url=f"https://images.example/{name}",
        source_url=f"https://source.example/{name}",
        source_name="Test source",
        search_query="Example University campus",
    )


def _save(image: Image.Image, *, format: str = "PNG", **kwargs: int) -> bytes:
    output = BytesIO()
    image.save(output, format=format, **kwargs)
    return output.getvalue()


def _scene(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = (
                x * 255 // max(size[0] - 1, 1),
                y * 255 // max(size[1] - 1, 1),
                (x + y) * 127 // max(sum(size) - 2, 1),
            )
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (size[0] // 8, size[1] // 5, size[0] // 2, size[1] * 4 // 5),
        fill=(240, 30, 40),
    )
    draw.ellipse(
        (size[0] // 2, size[1] // 4, size[0] * 7 // 8, size[1] * 3 // 4),
        fill=(20, 220, 100),
    )
    return image


def _different_scene(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, (20, 30, 210))
    draw = ImageDraw.Draw(image)
    draw.polygon(
        [(0, 0), (size[0], size[1] // 2), (0, size[1])],
        fill=(250, 220, 20),
    )
    draw.line((0, size[1], size[0], 0), fill=(0, 0, 0), width=5)
    return image


def _transport(files: dict[str, bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        content = files.get(request.url.path)
        if content is None:
            return httpx.Response(404)
        return httpx.Response(200, content=content, headers={"Content-Type": "image/png"})

    return httpx.MockTransport(handler)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_identical_file_returns_one_image(caplog: pytest.LogCaptureFixture) -> None:
    content = _save(_scene((96, 64)))
    files = {"/first.png": content, "/copy.png": content}
    caplog.set_level(logging.INFO, logger="app.services.image_deduplicator")

    async with httpx.AsyncClient(transport=_transport(files)) as client:
        result = await ImageDeduplicator(client).deduplicate(
            [_candidate("first.png"), _candidate("copy.png")]
        )

    assert len(result) == 1
    assert result[0].content_hash is not None
    assert result[0].perceptual_hash is not None
    assert "found 2 images" in caplog.text
    assert "removed 1 duplicates" in caplog.text
    assert "remaining 1 images" in caplog.text


@pytest.mark.anyio
async def test_same_photo_at_different_size_returns_one_image() -> None:
    original = _scene((96, 64))
    resized = original.resize((192, 128), Image.Resampling.LANCZOS)
    files = {
        "/original.png": _save(original),
        "/resized.png": _save(resized),
    }

    async with httpx.AsyncClient(transport=_transport(files)) as client:
        result = await ImageDeduplicator(
            client, perceptual_threshold=2
        ).deduplicate([_candidate("original.png"), _candidate("resized.png")])

    assert len(result) == 1


@pytest.mark.anyio
async def test_same_photo_after_jpeg_compression_returns_one_image() -> None:
    scene = _scene((160, 120))
    files = {
        "/quality-95.jpg": _save(scene, format="JPEG", quality=95),
        "/quality-65.jpg": _save(scene, format="JPEG", quality=65),
    }

    async with httpx.AsyncClient(transport=_transport(files)) as client:
        result = await ImageDeduplicator(
            client, perceptual_threshold=2
        ).deduplicate(
            [_candidate("quality-95.jpg"), _candidate("quality-65.jpg")]
        )

    assert len(result) == 1


@pytest.mark.anyio
async def test_different_photos_are_both_preserved() -> None:
    files = {
        "/first.png": _save(_scene((96, 64))),
        "/second.png": _save(_different_scene((96, 64))),
    }

    async with httpx.AsyncClient(transport=_transport(files)) as client:
        result = await ImageDeduplicator(
            client, perceptual_threshold=2
        ).deduplicate([_candidate("first.png"), _candidate("second.png")])

    assert len(result) == 2
    assert result[0].perceptual_hash != result[1].perceptual_hash


@pytest.mark.anyio
async def test_existing_hashes_do_not_trigger_download() -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("pre-hashed images must not be downloaded")

    image = _candidate("already-hashed.png").model_copy(
        update={"content_hash": "a" * 64, "perceptual_hash": "b" * 16}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        result = await ImageDeduplicator(client).deduplicate([image])

    assert result == [image]
