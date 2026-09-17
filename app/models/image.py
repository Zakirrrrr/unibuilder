from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ImageCategory(StrEnum):
    CAMPUS = "campus"
    CLASSROOM = "classroom"
    LIBRARY = "library"
    DORMITORY = "dormitory"
    STUDENT_LIFE = "student_life"
    FACILITIES = "facilities"
    OTHER = "other"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class ImageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    image_url: HttpUrl
    source_url: HttpUrl
    source_name: str = Field(min_length=1)
    search_query: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    author: str | None = None
    license: str | None = None
    category: ImageCategory = ImageCategory.OTHER
    is_real_photo: bool | None = None
    is_relevant: bool | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verification_reason: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    perceptual_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
