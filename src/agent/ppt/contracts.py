from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    points: list[str] = Field(min_length=1, max_length=6)
    layout: str = Field(min_length=1, max_length=1000)
    notes: str = Field(min_length=1, max_length=5000)
    reference_document_ids: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def bounded_points(self):
        if any(not p.strip() or len(p) > 500 for p in self.points):
            raise ValueError("Each slide point must contain 1–500 characters")
        return self


class DeckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    style: str = Field(min_length=1, max_length=2000)
    context: str = Field(default="", max_length=3000)
    pages: list[PageSpec] = Field(min_length=1, max_length=20)
    source_document_ids: list[str] = Field(default_factory=list, max_length=20)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_attempts: int = Field(ge=1, le=40)
    tokens: int | None = Field(default=None, ge=1)
    afp: int | None = Field(default=None, ge=1)
    microusd: int | None = Field(default=None, ge=1)
