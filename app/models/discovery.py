from pydantic import BaseModel, ConfigDict, Field

from app.models.image import ImageCandidate
from app.models.university import University


class ImageSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    university: University
    limit_per_query: int = Field(default=5, ge=1, le=5)


class ImageSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    images: list[ImageCandidate]
