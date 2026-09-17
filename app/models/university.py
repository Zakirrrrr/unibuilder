from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class University(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    city: str | None = None
    country: str | None = None
    official_domain: str | None = None
