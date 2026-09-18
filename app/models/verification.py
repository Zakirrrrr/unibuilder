from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.image import ImageCandidate, ImageCategory
from app.models.university import University


class ImageVerificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_real_photo: bool
    is_relevant: bool
    category: ImageCategory
    confidence: float = Field(ge=0.0, le=1.0)
    verification_status: Literal["confirmed", "likely", "uncertain", "rejected"]
    reason: str = Field(min_length=1, max_length=1000)


class ImageVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    university: University
    images: list[ImageCandidate] = Field(min_length=1, max_length=10)


class RankedImageDecision(ImageVerificationDecision):
    image_id: str
    quality_score: float = Field(ge=0, le=1)
    is_interesting: bool = False
    interest_reason: str = Field(default="", max_length=300)


class ImageSelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: list[RankedImageDecision] = Field(min_length=1, max_length=6)


class ImageVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: list[ImageCandidate]
