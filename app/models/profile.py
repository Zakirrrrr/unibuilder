from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.image import ImageCandidate, ImageCategory
from app.models.university import University


PROFILE_CATEGORIES = (
    ImageCategory.CAMPUS,
    ImageCategory.LIBRARY,
    ImageCategory.DORMITORY,
    ImageCategory.CLASSROOM,
    ImageCategory.STUDENT_LIFE,
    ImageCategory.FACILITIES,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def empty_profile_categories() -> dict[ImageCategory, list[ImageCandidate]]:
    return {category: [] for category in PROFILE_CATEGORIES}


class ProfileGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)


class ProfileStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: int = Field(ge=0)
    duplicates_removed: int = Field(ge=0)
    verified: int = Field(ge=0)
    rejected: int = Field(ge=0)


class UniversityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    university: University
    categories: dict[ImageCategory, list[ImageCandidate]] = Field(
        default_factory=empty_profile_categories
    )
    statistics: ProfileStatistics = Field(
        default_factory=lambda: ProfileStatistics(
            found=0, duplicates_removed=0, verified=0, rejected=0
        )
    )
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

    @field_validator("categories", mode="after")
    @classmethod
    def include_all_profile_categories(
        cls, categories: dict[ImageCategory, list[ImageCandidate]]
    ) -> dict[ImageCategory, list[ImageCandidate]]:
        return {
            category: list(categories.get(category, []))
            for category in PROFILE_CATEGORIES
        }
