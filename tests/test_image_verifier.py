import pytest

from app.models.image import (
    ImageCandidate,
    ImageCategory,
    VerificationStatus,
)
from app.models.university import University
from app.models.verification import ImageVerificationDecision
from app.services.ai_providers.base import (
    AIMalformedResponseError,
    ImageVerificationProvider,
)
from app.services.image_verifier import ImageVerifier


def _image(title: str | None = None) -> ImageCandidate:
    return ImageCandidate(
        image_url="https://upload.wikimedia.org/example.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Example.jpg",
        source_name="Wikimedia Commons",
        search_query="Example University campus",
        title=title,
    )


class FixedProvider(ImageVerificationProvider):
    def __init__(self, decision: ImageVerificationDecision) -> None:
        self._decision = decision

    async def verify(
        self, image: ImageCandidate, university: University
    ) -> ImageVerificationDecision:
        return self._decision


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("decision", "title", "expected"),
    [
        (
            ImageVerificationDecision(
                is_real_photo=True,
                is_relevant=True,
                category="library",
                confidence=0.9,
                verification_status="confirmed",
                reason="Explicit metadata and visual content agree.",
            ),
            "Nazarbayev University library",
            VerificationStatus.CONFIRMED,
        ),
        (
            ImageVerificationDecision(
                is_real_photo=True,
                is_relevant=True,
                category="campus",
                confidence=0.92,
                verification_status="likely",
                reason="The image looks compatible but has no identifying context.",
            ),
            "Modern building",
            VerificationStatus.LIKELY,
        ),
        (
            ImageVerificationDecision(
                is_real_photo=True,
                is_relevant=True,
                category="other",
                confidence=0.4,
                verification_status="uncertain",
                reason="There is not enough evidence.",
            ),
            None,
            VerificationStatus.UNCERTAIN,
        ),
        (
            ImageVerificationDecision(
                is_real_photo=False,
                is_relevant=False,
                category="other",
                confidence=0.95,
                verification_status="rejected",
                reason="This is a logo, not a photograph.",
            ),
            None,
            VerificationStatus.REJECTED,
        ),
    ],
)
async def test_verifier_assigns_conservative_status(
    decision: ImageVerificationDecision,
    title: str | None,
    expected: VerificationStatus,
) -> None:
    university = University(name="Nazarbayev University")
    result = await ImageVerifier(FixedProvider(decision)).verify(
        _image(title), university
    )

    assert result.verification_status == expected
    assert result.category == decision.category
    assert result.confidence == decision.confidence
    assert result.verification_reason == decision.reason


class MalformedThenValidProvider(ImageVerificationProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def verify(
        self, image: ImageCandidate, university: University
    ) -> ImageVerificationDecision:
        self.calls += 1
        if self.calls == 1:
            raise AIMalformedResponseError("invalid JSON")
        return ImageVerificationDecision(
            is_real_photo=True,
            is_relevant=True,
            category="campus",
            confidence=0.8,
            verification_status="likely",
            reason="Relevant context is present but not conclusive.",
        )


@pytest.mark.anyio
async def test_verify_many_continues_after_malformed_provider_json() -> None:
    provider = MalformedThenValidProvider()
    images = [_image("First"), _image("Second")]

    results = await ImageVerifier(provider).verify_many(
        images, University(name="Example University")
    )

    assert len(results) == 2
    assert results[0].verification_status == VerificationStatus.UNCERTAIN
    assert "structured JSON" in results[0].verification_reason
    assert results[1].verification_status == VerificationStatus.LIKELY
