from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.university import University


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class UniversityResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)


class UniversityResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResolutionStatus
    university: University | None = None
    candidates: list[University] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "UniversityResolutionResponse":
        if self.status == ResolutionStatus.RESOLVED and self.university is None:
            raise ValueError("resolved response requires a university")
        if self.status == ResolutionStatus.AMBIGUOUS and not self.candidates:
            raise ValueError("ambiguous response requires candidates")
        return self
