from datetime import timedelta

from t2s.planning.dates import explicit_date_in_text, parse_date_revision
from t2s.planning.evidence import date_supported, metric_supported, resolve_metric_reply, valid_quotes
from t2s.planning.models import Decision, Evidence, EvidenceField, PartialPlan, Plan
from t2s.planning.rules import normalize_plan, required_tables
from t2s.sessions.models import Choice, Session


def validate_decision(session: Session, catalog: dict, *, enforce_evidence: bool = True) -> None:
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
    if enforce_evidence:
        missing = _missing_evidence(session, decision, plan, catalog)
    if missing:
        _clarify(session, decision, plan, missing, reason="请补充明确依据：" + "、".join(missing))
    else:
        session.decision = decision.model_copy(update={"status": "query", "plan": plan})
        session.stage = "confirm"
        session.reason = "请核对查询计划后确认"


def _missing_evidence(session: Session, decision: Decision, plan: Plan, catalog: dict) -> list[EvidenceField]:
    missing: list[EvidenceField] = []
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
    return missing


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


def apply_explicit_date(session: Session) -> bool:
    decision = session.decision
    if (
        decision is None or decision.status != "query" or decision.plan is None
        or session.choice.start is not None or not session.messages or "date" in decision.missing_fields
    ):
        return False
    plan = decision.plan
    if plan.start is None or plan.end is None:
        return False
    span = explicit_date_in_text(session.messages[-1])
    if span is None:
        return False
    same_end = span.end - timedelta(days=1) if plan.end_inclusive else span.end
    if (plan.start, plan.end) == (span.start, same_end):
        return False
    session.decision = decision.model_copy(update={
        "plan": plan.model_copy(update={"start": span.start, "end": span.end, "end_inclusive": False}),
    })
    return True


def citation_diagnostics(session: Session) -> dict:
    decision = session.decision
    if decision is None:
        return {}
    plan = decision.plan
    expected: list[EvidenceField] = []
    if plan is not None:
        if plan.metric and session.choice.metric != plan.metric:
            expected.append("metric")
        if plan.start and plan.end and session.choice.start is None:
            expected.append("date")
        if plan.dimension != "none":
            expected.append("dimension")
        if plan.category is not None:
            expected.append("category")
    fields = ("metric", "date", "dimension", "category")
    evidence = decision.evidence
    return {
        "missing_citations": [field for field in expected if not evidence.get(field)],
        "invalid_citations": [field for field in fields if evidence.get(field)
                              and not valid_quotes(session.messages, evidence[field])],
        "citation_references": [f"{field}:{item.message}" for field in fields
                                for item in evidence.get(field, [])],
    }


def apply_metric_reply(session: Session, text: str, catalog: dict) -> bool:
    decision = session.decision
    if session.stage != "clarify" or decision is None or "metric" not in decision.missing_fields:
        return False
    metric = resolve_metric_reply(text, catalog)
    if metric is None:
        return False

    plan = (decision.plan or PartialPlan()).model_copy(update={"metric": metric})
    missing = [field for field in decision.missing_fields if field != "metric"]
    evidence = {**decision.evidence, "metric": [Evidence(message=len(session.messages), quote=text)]}
    session.messages.append(text)
    session.choice = session.choice.model_copy(update={"metric": None})
    session.decision = decision.model_copy(update={
        "status": "query",
        "plan": plan, "tables": required_tables(plan, catalog),
        "evidence": evidence, "missing_fields": missing,
    })
    return True


def apply_date_revision(session: Session, text: str) -> bool:
    decision = session.decision
    # 只继承自由问数中已经校验过的计划和证据。
    if session.stage != "confirm" or not session.messages or decision is None or decision.plan is None:
        return False
    revision = parse_date_revision(text)
    if revision is None:
        return False

    span, quote = revision
    evidence = {**decision.evidence, "date": [Evidence(message=len(session.messages), quote=quote)]}
    plan = decision.plan.model_copy(update={"start": span.start, "end": span.end, "end_inclusive": False})
    session.messages.append(text)
    # 新文字日期用原话校验，之前点选且保持不变的指标继续有效。
    session.choice = session.choice.model_copy(update={"start": None, "end": None})
    session.decision = decision.model_copy(update={"plan": plan, "evidence": evidence})
    return True


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
