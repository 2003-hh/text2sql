"""公开业务事件。应用层只传入允许展示的摘要字段。"""

from datetime import UTC, datetime
import json
import logging
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from t2s.planning.models import EvidenceField

logger = logging.getLogger(__name__)

# 固定事件名及结果方便后台查询，也避免把原始异常当成状态。
_EVENT_TYPES = {
    "session_created": ("session", "recorded"),
    "session_restored": ("session", "recorded"),
    "revision_conflict": ("session", "blocked"),
    "planner_started": ("model", "started"),
    "planner_finished": ("model", "success"),
    "planner_failed": ("model", "error"),
    "plan_validated": ("validation", "success"),
    "evidence_diagnostics": ("validation", "recorded"),
    "explicit_date_applied": ("validation", "recorded"),
    "plan_rejected": ("validation", "blocked"),
    "clarification_required": ("human", "waiting"),
    "approval_required": ("human", "waiting"),
    "workflow_resumed": ("human", "recorded"),
    "choice_applied": ("human", "success"),
    "date_revision_applied": ("human", "success"),
    "metric_reply_applied": ("human", "success"),
    "approval_accepted": ("human", "success"),
    "session_cancelled": ("human", "success"),
    "sql_compiled": ("query", "success"),
    "sql_started": ("sql", "started"),
    "sql_finished": ("sql", "success"),
    "sql_failed": ("sql", "error"),
    "sql_skipped": ("sql", "blocked"),
}
_METRICS = frozenset({"paid", "count", "net", "shipped_orders", "stock_qty"})
_DIMENSIONS = frozenset({"none", "day", "month", "category"})

Action = Literal["choose", "reply", "confirm", "cancel"]
ReasonCode = Literal[
    "stale_revision", "stage_conflict", "planner_failed", "plan_rejected",
    "insufficient_evidence", "missing_query_conditions", "already_claimed", "query_execution_failed",
]


class TraceEvent(BaseModel):
    schema_version: Literal["t2s_trace_v1"] = "t2s_trace_v1"
    event_id: str
    step: int = 0
    timestamp: datetime
    session_id: str
    request_id: str
    revision: int
    category: str
    name: str
    stage: str
    status: str
    duration_ms: int | None = None
    reason_code: ReasonCode | None = None
    summary: dict[str, str | int | list[str]] = Field(default_factory=dict)


class TracePage(BaseModel):
    events: list[TraceEvent]
    next_after: int
    has_more: bool = False
    history_trimmed: bool = False


class TraceStore(Protocol):
    def append(self, event: TraceEvent, dedup: str) -> None: ...

    def read(self, session_id: str, after: int, limit: int) -> TracePage: ...


class TraceRecorder:
    def __init__(self, store: TraceStore | None = None):
        self._store = store

    def emit(
        self, name: str, *, session_id: str, request_id: str, revision: int, stage: str,
        duration_ms: int | None = None, reason_code: ReasonCode | None = None,
        metric: str | None = None, dimension: str | None = None,
        missing_fields: list[EvidenceField] | None = None, row_count: int | None = None,
        submitted_revision: int | None = None, action: Action | None = None,
        source: Literal["question", "plan", "checkpoint"] | None = None,
        executions: int | None = None, once_per_revision: bool = False,
        missing_citations: list[EvidenceField] | None = None,
        invalid_citations: list[EvidenceField] | None = None,
        citation_references: list[str] | None = None,
    ) -> None:
        if self._store is None:
            return
        category, status = _EVENT_TYPES[name]
        if name == "plan_validated" and stage == "clarify":
            status = "waiting"
        # 这里没有可接收原话、参数值、模型理由或异常正文的字段。
        summary = {key: value for key, value in {
            "metric": metric if metric in _METRICS else None,
            "dimension": dimension if dimension in _DIMENSIONS else None,
            "missing_fields": missing_fields,
            "row_count": row_count,
            "submitted_revision": submitted_revision,
            "action": action,
            "source": source,
            "executions": executions,
            "missing_citations": missing_citations,
            "invalid_citations": invalid_citations,
            "citation_references": citation_references,
        }.items() if value is not None}
        event = TraceEvent(
            event_id=str(uuid4()), timestamp=datetime.now(UTC), session_id=session_id,
            request_id=request_id, revision=revision, category=category, name=name,
            stage=stage, status=status, duration_ms=duration_ms, reason_code=reason_code,
            summary=summary,
        )
        dedup = f"{name}:revision:{revision}" if once_per_revision else f"{name}:request:{request_id}"
        try:
            self._store.append(event, dedup)
        except RedisError as error:
            # 保留同一套脱敏摘要。trace 写失败由日志留证，业务继续运行。
            logger.warning("trace_storage_unavailable %s", json.dumps({
                "event": event.model_dump(mode="json", exclude={"step"}),
                "error_type": type(error).__name__,
            }, ensure_ascii=False))

    def read(self, session_id: str, after: int, limit: int) -> TracePage:
        if self._store is None:
            return TracePage(events=[], next_after=after)
        return self._store.read(session_id, after, limit)
