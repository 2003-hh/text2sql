from threading import Lock
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from t2s.observability.trace import TracePage, TraceRecorder
from t2s.planning.models import Decision, Plan
from t2s.planning.planner import Planner
from t2s.planning.rules import normalize_plan, required_tables
from t2s.sessions.models import Reply, Session
from t2s.workflow.graph import Claim, Execute, build_graph


class SessionNotFound(Exception):
    pass


class SessionConflict(Exception):
    pass


class SessionService:
    def __init__(self, saver: BaseCheckpointSaver, catalog: dict, execute: Execute, claim: Claim,
                 *, planner: Planner | None = None, trace: TraceRecorder | None = None):
        self._catalog = catalog
        self._trace = trace if trace is not None else TraceRecorder()
        self._graph = build_graph(saver, catalog, execute, claim, planner, self._trace)
        self._locks = tuple(Lock() for _ in range(32))

    @staticmethod
    def _config(id: str, request_id: str | None = None) -> dict:
        try:
            canonical = str(UUID(id))
        except ValueError as error:
            raise SessionNotFound from error
        configurable = {"thread_id": f"t2s-v1:{canonical}"}
        if request_id is not None:
            configurable["request_id"] = request_id
        return {"configurable": configurable}

    def _waiting_event(self, session: Session, request_id: str) -> None:
        if session.stage not in {"confirm", "clarify"}:
            return
        decision = session.decision
        self._trace.emit(
            "approval_required" if session.stage == "confirm" else "clarification_required",
            session_id=session.id, request_id=request_id, revision=session.revision, stage=session.stage,
            missing_fields=decision.missing_fields if session.stage == "clarify" and decision else None,
            reason_code="insufficient_evidence" if session.stage == "clarify" else None,
            once_per_revision=True,
        )

    def choices(self) -> dict[str, str]:
        values = {name: metric["name"] for name, metric in self._catalog["metrics"].items()}
        return {**values, "other": "都不是，重新描述"}

    def create(self, plan: Plan | None = None, *, question: str | None = None) -> Session:
        if question is not None:
            session = Session(id=str(uuid4()), stage="pending", reason="正在规划",
                              messages=[question.strip()])
        else:
            assert plan is not None
            plan = normalize_plan(plan, self._catalog)
            reason = "请核对查询计划后确认"
            session = Session(id=str(uuid4()), stage="confirm", reason=reason, decision=Decision(
                plan=plan, reason=reason, tables=required_tables(plan, self._catalog),
            ))
        request_id = str(uuid4())
        config = self._config(session.id, request_id)
        self._trace.emit(
            "session_created", session_id=session.id, request_id=request_id,
            revision=session.revision, stage=session.stage,
            source="question" if question is not None else "plan",
        )
        self._graph.invoke({"session": session.model_dump(mode="json")}, config, durability="sync")
        result = self._read(config)
        self._waiting_event(result, request_id)
        return result

    def get(self, id: str) -> Session:
        config = self._config(id)
        with self._locks[hash(config["configurable"]["thread_id"]) % len(self._locks)]:
            result = self._read(config)
            self._trace.emit(
                "session_restored", session_id=result.id, request_id=str(uuid4()),
                revision=result.revision, stage=result.stage, source="checkpoint",
                executions=result.executions,
            )
            return result

    def read_trace(self, id: str, after: int, limit: int) -> TracePage:
        # 查询轨迹不增加轨迹事件，也不推进图。
        session = self._read(self._config(id))
        return self._trace.read(session.id, after, limit)

    def _read(self, config: dict) -> Session:
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise SessionNotFound
        session = Session.model_validate(snapshot.values["session"])
        if not snapshot.next and session.stage in {"approved", "checking", "pending"}:
            session.stage = "error"
            session.reason = "上次处理未能确认完成，请新建会话"
        elif session.stage == "approved":
            # 在锁内读到批准中间态，说明上次推进没有成功写下终态。
            session.stage = "error"
            session.reason = "上次执行未能确认完成，请新建会话"
        return session

    def respond(self, id: str, reply: Reply) -> Session:
        request_id = str(uuid4())
        config = self._config(id, request_id)
        with self._locks[hash(config["configurable"]["thread_id"]) % len(self._locks)]:
            session = self._read(config)
            if session.stage not in {"confirm", "clarify"} or reply.revision != session.revision:
                self._trace.emit(
                    "revision_conflict", session_id=session.id, request_id=request_id,
                    revision=session.revision, stage=session.stage, action=reply.action,
                    submitted_revision=reply.revision,
                    reason_code="stale_revision" if reply.revision != session.revision else "stage_conflict",
                )
                raise SessionConflict
            if reply.action == "confirm" and session.stage != "confirm":
                raise ValueError("请先补齐查询条件")
            if reply.action == "choose":
                if session.stage != "clarify":
                    raise ValueError("只有待补充的会话可以点选")
                assert reply.choice is not None
                if reply.choice.metric is not None and reply.choice.metric not in self._catalog["metrics"]:
                    raise ValueError("未知指标，请重新描述")
            self._graph.invoke(Command(resume=reply.model_dump(mode="json")),
                               config, durability="sync")
            result = self._read(config)
            self._waiting_event(result, request_id)
            return result
