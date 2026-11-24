from collections.abc import Callable
from datetime import timedelta
import logging
from time import perf_counter
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from t2s.observability.trace import TraceRecorder
from t2s.planning.decision_rules import (
    apply_choice, apply_date_revision, apply_metric_reply, validate_decision,
)
from t2s.planning.models import Plan
from t2s.planning.planner import Planner, PlannerError
from t2s.query.compiler import CompiledQuery, compile_sql
from t2s.query.executor import QueryExecutionError, Rows
from t2s.sessions.models import Choice, Reply, Session

logger = logging.getLogger(__name__)
Execute = Callable[[CompiledQuery], Rows]
Claim = Callable[[str, int], bool]


class WorkflowState(TypedDict):
    session: dict


def build_graph(saver: BaseCheckpointSaver, catalog: dict, execute: Execute, claim: Claim,
                planner: Planner | None = None, trace: TraceRecorder | None = None):
    trace = trace if trace is not None else TraceRecorder()

    def record(name: str, session: Session, config: RunnableConfig, **details) -> None:
        trace.emit(name, session_id=session.id, request_id=config["configurable"]["request_id"],
                   revision=session.revision, stage=session.stage, **details)

    def plan_question(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        session = Session.model_validate(state["session"])
        record("planner_started", session, config)
        started = perf_counter()
        try:
            if planner is None:
                raise PlannerError("自然语言规划器未配置")
            session.decision = planner(session.messages)
        except PlannerError as error:
            logger.warning("Planner failed for session %s (%s)", session.id,
                           type(error.__cause__ or error).__name__)
            session.stage = "error"
            session.reason = str(error)
            session.decision = None
            record("planner_failed", session, config, reason_code="planner_failed",
                   duration_ms=int((perf_counter() - started) * 1000))
        else:
            session.stage = "checking"
            record("planner_finished", session, config,
                   duration_ms=int((perf_counter() - started) * 1000))
        return {"session": session.model_dump(mode="json")}

    def plan_validation(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        session = Session.model_validate(state["session"])
        started = perf_counter()
        validate_decision(session, catalog)
        decision = session.decision
        plan = decision.plan if decision is not None else None
        record(
            "plan_rejected" if session.stage == "reject" else "plan_validated", session, config,
            duration_ms=int((perf_counter() - started) * 1000),
            reason_code="plan_rejected" if session.stage == "reject" else None,
            missing_fields=decision.missing_fields if session.stage == "clarify" and decision else None,
            metric=plan.metric if plan else None, dimension=plan.dimension if plan else None,
        )
        return {"session": session.model_dump(mode="json")}

    def human_feedback(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        session = Session.model_validate(state["session"])
        # 恢复时重新进入这个节点。版本和状态变化放在 interrupt 之后。
        reply = Reply.model_validate(interrupt({
            "stage": session.stage, "revision": session.revision,
        }))
        session.revision += 1
        record("workflow_resumed", session, config, action=reply.action,
               submitted_revision=reply.revision, once_per_revision=True)
        if reply.action == "confirm":
            session.stage = "approved"
            session.reason = "计划已获批准"
            record("approval_accepted", session, config, submitted_revision=reply.revision,
                   once_per_revision=True)
        elif reply.action == "cancel":
            session.stage = "cancelled"
            session.reason = "查询已取消"
            record("session_cancelled", session, config, executions=session.executions,
                   once_per_revision=True)
        elif reply.action == "choose":
            assert reply.choice is not None
            apply_choice(session, reply.choice, catalog)
            session.stage = "checking"
            record("choice_applied", session, config, once_per_revision=True)
        else:
            assert reply.text is not None
            text = reply.text.strip()
            if apply_date_revision(session, text):
                session.stage = "checking"
                record("date_revision_applied", session, config, once_per_revision=True)
                return {"session": session.model_dump(mode="json")}
            if apply_metric_reply(session, text, catalog):
                session.stage = "checking"
                assert session.decision is not None and session.decision.plan is not None
                record("metric_reply_applied", session, config, metric=session.decision.plan.metric,
                       once_per_revision=True)
                return {"session": session.model_dump(mode="json")}
            # 其他文字修改保留原有历史和明确点选，由最新消息覆盖旧语义。
            if session.choice.metric is not None:
                metric = catalog["metrics"][session.choice.metric]["name"]
                session.messages.append(f"此前点选的指标：{metric}")
            if session.choice.start is not None and session.choice.end is not None:
                inclusive_end = session.choice.end - timedelta(days=1)
                session.messages.append(f"此前点选的日期：{session.choice.start}至{inclusive_end}")
            session.messages.append(text)
            session.choice = Choice()
            session.warnings = []
            session.stage = "pending"
        return {"session": session.model_dump(mode="json")}

    def sql_execute(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        session = Session.model_validate(state["session"])
        if session.stage != "approved" or session.decision is None or session.decision.plan is None:
            raise RuntimeError("Confirmation required")
        plan = Plan.model_validate(session.decision.plan.model_dump())
        query = compile_sql(plan, catalog)
        record("sql_compiled", session, config, metric=plan.metric, dimension=plan.dimension,
               once_per_revision=True)
        # 先持久领取，再查库。进程中断留下的领取记录阻止自动重发。
        if not claim(session.id, session.revision):
            session.stage = "error"
            session.reason = "该版本已有执行记录，无法安全重试，请新建会话"
            record("sql_skipped", session, config, reason_code="already_claimed", once_per_revision=True)
        else:
            record("sql_started", session, config, once_per_revision=True)
            started = perf_counter()
            try:
                session.rows = execute(query)
            except QueryExecutionError as error:
                logger.warning("Query failed for session %s (%s)", session.id,
                               type(error.__cause__ or error).__name__)
                session.stage = "error"
                session.reason = str(error)
                record("sql_failed", session, config, reason_code="query_execution_failed",
                       duration_ms=int((perf_counter() - started) * 1000), once_per_revision=True)
            else:
                session.stage = "done"
                session.reason = "查询完成"
                session.executions += 1
                record("sql_finished", session, config, row_count=len(session.rows),
                       duration_ms=int((perf_counter() - started) * 1000), once_per_revision=True)
        return {"session": session.model_dump(mode="json")}

    def stage(state: WorkflowState) -> str:
        return state["session"]["stage"]

    graph = StateGraph(WorkflowState)
    graph.add_node("planner", plan_question)
    graph.add_node("plan_validation", plan_validation)
    graph.add_node("human_feedback", human_feedback)
    graph.add_node("sql_execute", sql_execute)
    graph.add_conditional_edges(START, stage, {"pending": "planner", "confirm": "human_feedback"})
    graph.add_conditional_edges("planner", stage, {"checking": "plan_validation", "error": END})
    graph.add_conditional_edges("plan_validation", stage, {
        "confirm": "human_feedback", "clarify": "human_feedback", "reject": END,
    })
    graph.add_conditional_edges("human_feedback", stage, {
        "approved": "sql_execute", "cancelled": END, "pending": "planner",
        "checking": "plan_validation",
    })
    graph.add_edge("sql_execute", END)
    return graph.compile(checkpointer=saver)
