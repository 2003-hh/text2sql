from evals.comparison.planners import build_rule_planner
from evals.comparison.run import outcome
from t2s.infrastructure.catalog import load_catalog


def test_rule_planner_clarifies_ambiguous_metric_and_keeps_date_after_reply():
    planner = build_rule_planner(None, load_catalog())
    messages = ["2026年6月12日按天看销售额"]
    initial = planner(messages)
    assert initial.missing_fields == ["metric"]
    result = planner([*messages, "采用实付金额。"])
    assert result.status == "query"
    assert result.plan.metric == "paid"
    assert result.plan.start.isoformat() == "2026-06-12"
    assert result.evidence["metric"][0].message == 1
    assert result.evidence["date"][0].message == 0


def test_outcome_flags_wrong_confirmation_even_without_a_query():
    result = {
        "status": "fail",
        "turns": [{"action": "ask", "response_ms": 12,
                   "actual": {"stage": "confirm"}, "expected": {"stage": "clarify"}}],
    }
    assert outcome(result) == {"wrong_card": True, "extra_questions": 0, "last_card_ms": None}


def test_outcome_excludes_execution_time_from_the_correct_card():
    checked = {"metric": True, "date": True, "dimension": True, "category": True}
    result = {
        "status": "pass",
        "turns": [
            {"action": "ask", "response_ms": 13, "actual": {"stage": "clarify", "missing_fields": ["date"]},
             "expected": {"stage": "clarify", "missing_fields": ["date"]}},
            {"action": "reply", "response_ms": 7, "actual": {"stage": "confirm"},
             "expected": {"stage": "confirm"}, "plan_checks": checked},
            {"action": "confirm", "response_ms": 555},
        ],
    }
    assert outcome(result) == {"wrong_card": False, "extra_questions": 0, "last_card_ms": 20}
