from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class PartialPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str | None = None
    start: date | None = None
    end: date | None = None
    dimension: Literal["none", "day", "month", "category"] = "none"
    category: str | None = None
    end_inclusive: StrictBool = True

    @field_validator("start", "end", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if value is None or type(value) is date or (
            isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        ):
            return value
        raise ValueError("日期格式须为 YYYY-MM-DD")


class Plan(PartialPlan):
    metric: str
    start: date
    end: date
    dimension: Literal["none", "day", "month", "category"]
    end_inclusive: StrictBool


EvidenceField = Literal["metric", "date", "dimension", "category"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: int = Field(ge=0, strict=True)
    quote: str = Field(min_length=1)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["query", "clarify", "reject"] = "query"
    reason: str
    plan: PartialPlan | None = None
    tables: list[str] = Field(default_factory=list)
    missing_fields: list[EvidenceField] = Field(default_factory=list)
    evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
