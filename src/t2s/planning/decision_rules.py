from t2s.planning.evidence import date_supported, metric_supported, valid_quotes
from t2s.planning.models import Decision, EvidenceField, PartialPlan, Plan
from t2s.planning.rules import normalize_plan, required_tables
from t2s.sessions.models import Choice, Session


def validate_decision(session: Session, catalog: dict) -> None:
    decision = session.decision
    if decision is None:
        raise RuntimeError("Missing decision")
    session.reason = decision.reason
    if decision.status == "reject":
        session.stage = "reject"
        return

    partial = decision.plan or PartialPlan()
    missing: list[EvidenceField] = list(decision.missing_fields)
    if partial.metric is None and "metric" not in missing:
        missing.append("metric")
    if (partial.start is None or partial.end is None) and "date" not in missing:
        missing.append("date")
    if missing:
        _clarify(session, decision, partial, missing)
        return

    try:
        plan = normalize_plan(Plan.model_validate(partial.model_dump()), catalog)
    except ValueError as error:
        _reject(session, decision, str(error))
        return
    # SQL is compiled from the catalog, so use the same plan to derive the display tables.
    decision = decision.model_copy(update={"tables": required_tables(plan, catalog)})
    session.warnings = []
    evidence = decision.evidence
    if session.choice.metric != plan.metric and not metric_supported(
        session.messages, evidence.get("metric", []), plan.metric, catalog["ambiguousTerms"]
    ):
        missing.append("metric")
    if (session.choice.start, session.choice.end) != (plan.start, plan.end) and not date_supported(
        session.messages, evidence.get("date", []), plan
    ):
        missing.append("date")
    if plan.dimension != "none" and not valid_quotes(session.messages, evidence.get("dimension", [])):
        missing.append("dimension")
    if plan.category is not None and not valid_quotes(session.messages, evidence.get("category", [])):
        missing.append("category")
    if missing:
        _clarify(session, decision, plan, missing, reason="请补充明确依据：" + "、".join(missing))
    else:
        session.decision = decision.model_copy(update={"status": "query", "plan": plan})
        session.stage = "confirm"
        session.reason = "请核对查询计划后确认"


def _clarify(session: Session, decision: Decision, plan: PartialPlan,
             missing: list[EvidenceField], *, reason: str | None = None) -> None:
    reason = reason or (decision.reason if decision.status == "clarify" else
                        "请补充查询条件：" + "、".join(missing))
    session.stage = "clarify"
    session.reason = reason
    session.decision = decision.model_copy(update={
        "status": "clarify", "plan": plan, "reason": reason, "missing_fields": missing,
    })


def _reject(session: Session, decision: Decision, reason: str) -> None:
    session.stage = "reject"
    session.reason = reason
    session.decision = decision.model_copy(update={"status": "reject", "reason": reason})


def apply_choice(session: Session, choice: Choice, catalog: dict) -> None:
    decision = session.decision
    if decision is None or decision.plan is None:
        raise RuntimeError("Missing partial plan")
    changes = choice.model_dump(exclude_none=True)
    session.choice = session.choice.model_copy(update=changes)
    if choice.start is not None:
        changes["end_inclusive"] = False
    plan = decision.plan.model_copy(update=changes)
    filled = {"metric"} if choice.metric is not None else set()
    if choice.start is not None:
        filled.add("date")
    missing = [field for field in decision.missing_fields if field not in filled]
    tables = (required_tables(plan, catalog) if plan.metric is not None
              else decision.tables)
    session.decision = decision.model_copy(update={
        "status": "clarify" if missing else "query",
        "reason": "已采用用户选项",
        "plan": plan, "tables": tables, "missing_fields": missing,
    })
