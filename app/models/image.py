from enum import StrEnum
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PrivateAttr


class ImageCategory(StrEnum):
    CAMPUS = "campus"
    CLASSROOM = "classroom"
    LIBRARY = "library"
    DORMITORY = "dormitory"
    STUDENT_LIFE = "student_life"
    FACILITIES = "facilities"
    SPORT = "sport"
    LABORATORY = "laboratory"
    CITY = "city"
    OTHER = "other"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class ImageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _downloaded_bytes: bytes | None = PrivateAttr(default=None)

    id: UUID = Field(default_factory=uuid4)
    image_url: HttpUrl
    source_url: HttpUrl
    source_name: str = Field(min_length=1)
    search_query: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    author: str | None = None
    license: str | None = None
    published_at: str | None = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    category: ImageCategory = ImageCategory.OTHER
    is_real_photo: bool | None = None
    is_relevant: bool | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    is_primary: bool = False
    is_interesting: bool = False
    interest_reason: str | None = None
    verification_reason: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    perceptual_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
