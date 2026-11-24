from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t2s.planning.models import Decision, PartialPlan, Plan
from t2s.query.executor import Rows


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Plan | None = None
    question: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def exactly_one_input(self):
        if (self.plan is None) == (self.question is None):
            raise ValueError("请提交 question 或 plan 其中一项")
        if self.question is not None and not self.question.strip():
            raise ValueError("问题不能为空")
        return self


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str | None = None
    start: date | None = None
    end: date | None = None

    _calendar_dates = field_validator("start", "end", mode="before")(
        PartialPlan.require_calendar_date
    )


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    action: Literal["confirm", "cancel", "choose", "reply"]
    text: str | None = Field(default=None, max_length=4000)
    choice: Choice | None = None

    @model_validator(mode="after")
    def check_payload(self):
        if self.action == "reply":
            if not self.text or not self.text.strip() or self.choice is not None:
                raise ValueError("请填写修改内容")
        elif self.action == "choose":
            if self.choice is None or self.text is not None:
                raise ValueError("请提供点选内容")
            if (self.choice.start is None) != (self.choice.end is None):
                raise ValueError("开始日期和结束日期必须一起提交")
            if self.choice.metric is None and self.choice.start is None:
                raise ValueError("请至少选择一项")
        elif self.text is not None or self.choice is not None:
            raise ValueError("确认和取消只需要版本")
        return self


class Session(BaseModel):
    id: str
    stage: Literal["pending", "checking", "confirm", "clarify", "reject", "approved",
                   "done", "cancelled", "error"]
    reason: str
    decision: Decision | None = None
    revision: int = 0
    executions: int = 0
    rows: Rows = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    choice: Choice = Field(default_factory=Choice)
