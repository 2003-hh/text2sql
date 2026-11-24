from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from t2s.infrastructure.catalog import load_catalog
from t2s.main import create_app
from t2s.planning.models import Decision
from t2s.sessions.service import SessionService


def candidate(quote="实付金额", start="2026-09-01", end="2026-09-30", category=None):
    return {
        "status": "query", "reason": "固定测试回应",
        "plan": {"metric": "paid", "start": start, "end": end, "dimension": "day",
                 "category": category, "end_inclusive": True},
        "tables": ["orders"] if category is None else ["orders", "order_items", "products"],
        "evidence": {"metric": [{"message": 0, "quote": quote}],
                     "date": [{"message": 0, "quote": "2026年9月"}],
                     "dimension": [{"message": 0, "quote": "每天"}],
                     **({"category": [{"message": 0, "quote": category}]} if category else {})},
    }


def setup(result):
    planner = Mock(return_value=Decision.model_validate(result))
    executor = Mock(return_value=[["2026-09-01", 100]])
    service = SessionService(InMemorySaver(), load_catalog(), executor, Mock(return_value=True),
                             planner=planner)
    return TestClient(create_app(probes={}, sessions=lambda: service)), planner, executor


def test_natural_question_waits_with_evidence():
    client, planner, executor = setup(candidate())
    question = "2026年9月每天实付金额是多少？"
    with client:
        response = client.post("/sessions", json={"question": question})
    assert response.status_code == 200
    session = response.json()
    assert session["stage"] == "confirm"
    assert session["decision"]["plan"]["end"] == "2026-10-01"
    planner.assert_called_once()
    executor.assert_not_called()


def test_ambiguous_metric_and_preservation_of_conditions():
    client, planner, executor = setup(candidate("销售额", category="键盘"))
    with client:
        session = client.post("/sessions", json={
            "question": "2026年9月每天键盘销售额是多少？"
        }).json()
        assert session["stage"] == "clarify"
        assert session["decision"]["missing_fields"] == ["metric"]
        url = f"/sessions/{session['id']}/respond"
        assert client.post(url, json={"revision": 0, "action": "confirm"}).status_code == 400
        result = client.post(url, json={"revision": 0, "action": "choose",
                                        "choice": {"metric": "paid"}}).json()
        assert (result["stage"], result["revision"]) == ("confirm", 1)
        assert result["decision"]["plan"]["category"] == "键盘"
        assert result["decision"]["plan"]["dimension"] == "day"
        assert client.post(url, json={"revision": 0, "action": "confirm"}).status_code == 409
        assert client.get("/choices").json()["other"] == "都不是，重新描述"
    planner.assert_called_once()
    executor.assert_not_called()


def test_only_filling_dates_preserves_group_and_filter():
    result = candidate(start=None, end=None, category="键盘")
    result["status"] = "clarify"
    result["missing_fields"] = ["date"]
    result["evidence"].pop("date")
    client, planner, executor = setup(result)
    with client:
        session = client.post("/sessions", json={"question": "每天键盘实付金额"}).json()
        reply = {"revision": 0, "action": "choose",
                 "choice": {"start": "2026-09-01", "end": "2026-10-01"}}
        response = client.post(f"/sessions/{session['id']}/respond", json=reply)
        assert response.status_code == 200
        updated = response.json()
        assert updated["stage"] == "confirm"
        assert not updated["decision"]["plan"]["end_inclusive"]
        assert updated["decision"]["plan"]["end"] == "2026-10-01"
        assert updated["decision"]["plan"]["category"] == "键盘"
        assert updated["decision"]["plan"]["dimension"] == "day"
    planner.assert_called_once()
    executor.assert_not_called()


def test_reply_passes_original_and_new_messages_to_planner():
    client, planner, executor = setup(candidate("销售额"))
    with client:
        question = "2026年9月每天销售额是多少？"
        by_paid = candidate()
        by_paid["evidence"]["metric"][0]["message"] = 1
        planner.side_effect = [Decision.model_validate(candidate("销售额")),
                               Decision.model_validate(by_paid)]
        session = client.post("/sessions", json={"question": question}).json()
        response = client.post(f"/sessions/{session['id']}/respond",
                               json={"revision": 0, "action": "reply", "text": "按实付金额算"})
        assert response.json()["stage"] == "confirm"
        assert response.json()["messages"] == [question, "按实付金额算"]
        assert planner.call_args.args[0] == [question, "按实付金额算"]
    executor.assert_not_called()


def test_schema_with_invalid_evidence_keys_cannot_be_used():
    result = candidate()
    result["evidence"] = {"1": [{"message": 0, "quote": "实付金额"}]}
    client, _, executor = setup(result)
    with client:
        response = client.post("/sessions", json={"question": "2026年9月每天实付金额"})
        assert response.json()["stage"] == "clarify"
    executor.assert_not_called()


def test_backend_derives_tables_from_category_filter():
    result = candidate(category="键盘")
    result["tables"] = ["orders", "unknown_table"]
    client, _, executor = setup(result)
    with client:
        session = client.post("/sessions", json={
            "question": "2026年9月每天键盘实付金额是多少？"
        }).json()
        assert session["stage"] == "confirm"
        assert session["decision"]["tables"] == ["order_items", "orders", "products"]
        executor.assert_not_called()
        response = client.post(f"/sessions/{session['id']}/respond",
                               json={"revision": session["revision"], "action": "confirm"})
        assert response.json()["stage"] == "done"
        query = executor.call_args.args[0]
        assert "JOIN order_items" in query.text
        assert "JOIN products" in query.text
        assert "unknown_table" not in query.text
        assert query.params[-1] == "键盘"


def test_missing_table_suggestions_do_not_bypass_evidence():
    result = candidate(quote="销售额")
    result["tables"] = []
    client, _, executor = setup(result)
    with client:
        session = client.post("/sessions", json={
            "question": "2026年9月每天销售额是多少？"
        }).json()
    assert session["stage"] == "clarify"
    assert session["decision"]["missing_fields"] == ["metric"]
    assert session["decision"]["tables"] == ["orders"]
    executor.assert_not_called()


def test_explicit_date_revision_preserves_validated_conditions_without_planner():
    client, planner, executor = setup(candidate(category="键盘"))
    with client:
        session = client.post("/sessions", json={
            "question": "2026年9月每天键盘实付金额是多少？"
        }).json()
        url = f"/sessions/{session['id']}/respond"
        assert session["stage"] == "confirm"
        revised = client.post(url, json={
            "revision": 0, "action": "reply",
            "text": "只把日期改成 2026年9月10日到15日，其他照旧。"
        }).json()

        assert (revised["stage"], revised["revision"], revised["executions"]) == ("confirm", 1, 0)
        assert revised["decision"]["plan"] == {
            **session["decision"]["plan"], "start": "2026-09-10", "end": "2026-09-16",
        }
        evidence = revised["decision"]["evidence"]
        for field in ("metric", "dimension", "category"):
            assert evidence[field] == session["decision"]["evidence"][field]
        assert evidence["date"] == [{"message": 1, "quote": "2026年9月10日到15日"}]
        assert client.post(url, json={"revision": 0, "action": "confirm"}).status_code == 409
        planner.assert_called_once()
        executor.assert_not_called()

        done = client.post(url, json={"revision": 1, "action": "confirm"}).json()
        assert (done["stage"], done["executions"]) == ("done", 1)
        assert [str(value) for value in executor.call_args.args[0].params] == [
            "2026-09-10", "2026-09-16", "键盘",
        ]


@pytest.mark.parametrize("text", [
    "日期改为上次那几天，其他条件照旧。",
    "日期改为2026年9月10日至9月15日或2026年9月18日至9月20日。",
    "日期改为9月10日到15日。",
])
def test_unhandled_date_revisions_keep_using_planner(text):
    client, planner, executor = setup(candidate())
    unclear = candidate(start=None, end=None)
    unclear["status"] = "clarify"
    unclear["missing_fields"] = ["date"]
    planner.side_effect = [Decision.model_validate(candidate()), Decision.model_validate(unclear)]
    with client:
        question = "2026年9月每天实付金额是多少？"
        session = client.post("/sessions", json={"question": question}).json()
        assert session["stage"] == "confirm"
        updated = client.post(f"/sessions/{session['id']}/respond", json={
            "revision": 0, "action": "reply", "text": text,
        }).json()
        assert updated["stage"] == "clarify"
        assert updated["decision"]["missing_fields"] == ["date"]
        assert planner.call_args.args[0] == [question, text]
    executor.assert_not_called()


def test_date_and_metric_changes_replace_both_values():
    client, planner, executor = setup(candidate())
    changed = candidate(start="2026-09-10", end="2026-09-15")
    changed["plan"]["metric"] = "count"
    changed["evidence"]["metric"] = [{"message": 1, "quote": "支付成功订单数"}]
    changed["evidence"]["date"] = [{"message": 1, "quote": "2026年9月10日到15日"}]
    planner.side_effect = [Decision.model_validate(candidate()), Decision.model_validate(changed)]
    with client:
        session = client.post("/sessions", json={"question": "2026年9月每天实付金额"}).json()
        revised = client.post(f"/sessions/{session['id']}/respond", json={
            "revision": 0, "action": "reply",
            "text": "日期改为2026年9月10日到15日，同时改成支付成功订单数。",
        }).json()
    assert (revised["stage"], revised["revision"], revised["executions"]) == ("confirm", 1, 0)
    assert revised["decision"]["plan"]["metric"] == "count"
    assert revised["decision"]["plan"]["end"] == "2026-09-16"
    assert planner.call_count == 2
    executor.assert_not_called()
