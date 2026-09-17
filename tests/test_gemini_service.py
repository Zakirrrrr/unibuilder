import base64
import json

import httpx
import pytest

from app.models.image import ImageCandidate, ImageCategory
from app.models.university import University
from app.services.ai_providers.base import (
    AIMalformedResponseError,
    AIProviderConfigurationError,
)
from app.services.ai_providers.gemini import GeminiService


IMAGE_BYTES = b"fake-jpeg-bytes"


def _image() -> ImageCandidate:
    return ImageCandidate(
        image_url="https://upload.wikimedia.org/example.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Example.jpg",
        source_name="Wikimedia Commons",
        search_query="Nazarbayev University campus",
        title="Nazarbayev University campus",
        description="Campus in Astana",
    )


def _decision(**overrides: object) -> str:
    value: dict[str, object] = {
        "is_real_photo": True,
        "is_relevant": True,
        "category": "campus",
        "confidence": 0.91,
        "verification_status": "confirmed",
        "reason": "The metadata names the university and matches the photo.",
    }
    value.update(overrides)
    return json.dumps(value)


class FakeInteraction:
    def __init__(self, output_text: str | None) -> None:
        self.output_text = output_text


class FakeInteractions:
    def __init__(self, outputs: list[str | None]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeInteraction(self.outputs.pop(0))


class FakeGeminiClient:
    def __init__(self, outputs: list[str | None]) -> None:
        self.interactions = FakeInteractions(outputs)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1


def _transport(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=IMAGE_BYTES, headers={"Content-Type": "image/jpeg"}
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_gemini_service_sends_multimodal_structured_request() -> None:
    sdk_client = FakeGeminiClient([_decision()])
    limiter = CountingLimiter()
    university = University(
        name="Nazarbayev University",
        city="Astana",
        country="Kazakhstan",
        official_domain="nu.edu.kz",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        service = GeminiService(
            client,
            api_key="test-key",
            model="test-gemini-model",
            retry_attempts=1,
            rate_limiter=limiter,
            sdk_client=sdk_client,
        )
        result = await service.verify(_image(), university)

    call = sdk_client.interactions.calls[0]
    assert call["model"] == "test-gemini-model"
    assert call["store"] is False
    assert call["response_format"]["mime_type"] == "application/json"
    assert "verification_status" in call["response_format"]["schema"]["properties"]
    assert call["input"][0]["type"] == "text"
    assert "Nazarbayev University" in call["input"][0]["text"]
    assert call["input"][1]["type"] == "image"
    assert base64.b64decode(call["input"][1]["data"]) == IMAGE_BYTES
    assert call["input"][1]["mime_type"] == "image/jpeg"
    assert result.category == ImageCategory.CAMPUS
    assert result.verification_status == "confirmed"
    assert limiter.calls == 1


@pytest.mark.anyio
async def test_gemini_service_retries_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("app.services.ai_providers.gemini.asyncio.sleep", no_sleep)
    sdk_client = FakeGeminiClient(["{bad-json", _decision(is_relevant=False)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        service = GeminiService(
            client,
            api_key="test-key",
            retry_attempts=2,
            rate_limiter=CountingLimiter(),
            sdk_client=sdk_client,
        )
        result = await service.verify(
            _image(), University(name="Nazarbayev University")
        )

    assert len(sdk_client.interactions.calls) == 2
    assert result.is_relevant is False


@pytest.mark.anyio
async def test_gemini_service_retries_rate_limited_image_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(delay: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return _transport(request)

    monkeypatch.setattr("app.services.ai_providers.gemini.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = GeminiService(
            client,
            api_key="test-key",
            retry_attempts=1,
            rate_limiter=CountingLimiter(),
            sdk_client=FakeGeminiClient([_decision()]),
        )
        result = await service.verify(
            _image(), University(name="Nazarbayev University")
        )

    assert attempts == 2
    assert result.is_relevant is True


@pytest.mark.anyio
async def test_gemini_service_reports_exhausted_malformed_json() -> None:
    sdk_client = FakeGeminiClient(["not-json"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        service = GeminiService(
            client,
            api_key="test-key",
            retry_attempts=1,
            rate_limiter=CountingLimiter(),
            sdk_client=sdk_client,
        )
        with pytest.raises(AIMalformedResponseError):
            await service.verify(_image(), University(name="Example University"))


@pytest.mark.anyio
async def test_gemini_service_requires_api_key_before_download() -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("image must not be downloaded without an API key")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        service = GeminiService(client, api_key="", retry_attempts=1)
        with pytest.raises(AIProviderConfigurationError):
            await service.verify(_image(), University(name="Example University"))
