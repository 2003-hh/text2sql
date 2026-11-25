from unittest.mock import Mock

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from redis.exceptions import ConnectionError

from t2s.infrastructure.catalog import load_catalog
from t2s.main import create_app
from t2s.observability.trace import TracePage, TraceRecorder
from t2s.planning.models import Decision
from t2s.query.executor import QueryExecutionError
from t2s.sessions.service import SessionService


class MemoryEvents:
    def __init__(self):
        self.events = {}
        self.dedup = set()

    def append(self, event, dedup):
        key = (event.session_id, dedup)
        if key in self.dedup:
            return
        self.dedup.add(key)
        events = self.events.setdefault(event.session_id, [])
        events.append(event.model_copy(update={"step": len(events) + 1}))

    def read(self, session_id, after, limit):
        events = [event for event in self.events.get(session_id, []) if event.step > after]
        page = events[:limit]
        return TracePage(events=page, next_after=page[-1].step if page else after,
                         has_more=len(events) > limit)


def _setup(*, store=None, executor=None):
    store = store if store is not None else MemoryEvents()
    recorder = TraceRecorder(store)
    executor = executor if executor is not None else Mock(return_value=[["2026-09-01", 456789]])
    planner = Mock(return_value=Decision.model_validate({
        "status": "clarify", "reason": "PRIVATE_MODEL_REASON",
        "plan": {"metric": "paid", "dimension": "day", "category": "键盘"},
        "tables": ["orders", "order_items", "products"], "missing_fields": ["date"],
        "evidence": {"metric": [{"message": 0, "quote": "实付金额"}],
                     "dimension": [{"message": 0, "quote": "每天"}],
                     "category": [{"message": 0, "quote": "键盘"}]},
    }))
    saver = InMemorySaver()

    def app():
        service = SessionService(saver, load_catalog(), executor, Mock(return_value=True),
                                 planner=planner, trace=recorder)
        return TestClient(create_app(probes={}, sessions=lambda: service))

    return app, executor


def test_trace_tells_the_story_across_app_recreation():
    app, executor = _setup()
    with app() as c:
        pending = c.post("/sessions", json={"question": "每天键盘实付金额 PRIVATE_INPUT"}).json()
        url = "/sessions/" + pending["id"]
        before = c.get(url + "/trace").json()["events"]
        assert [item["name"] for item in before] == [
            "session_created", "planner_started", "planner_finished",
            "plan_validated", "clarification_required",
        ]
        assert before[-1]["summary"]["missing_fields"] == ["date"]
        assert len({item["request_id"] for item in before}) == 1
        chosen = c.post(url + "/respond", json={"revision": 0, "action": "choose",
                           "choice": {"start": "2026-09-01", "end": "2026-10-01"}}).json()
        assert chosen["stage"] == "confirm"
        executor.assert_not_called()

    with app() as c:
        assert c.get(url).json() == chosen
        assert c.post(url + "/respond", json={"revision": 0, "action": "confirm"}).status_code == 409
        assert c.post(url + "/respond", json={"revision": 1, "action": "confirm"}).status_code == 200
        assert c.post(url + "/respond", json={"revision": 1, "action": "confirm"}).status_code == 409
        response = c.get(url + "/trace")
        events = response.json()["events"]
        names = [item["name"] for item in events]
        assert names.count("sql_started") == names.count("sql_finished") == 1
        assert names.count("approval_required") == names.count("approval_accepted") == 1
        assert names.count("revision_conflict") == 2
        started, finished = [item for item in events if item["category"] == "sql"]
        assert started["request_id"] == finished["request_id"] != before[0]["request_id"]
        assert started["revision"] == finished["revision"] == 2
        assert finished["summary"] == {"row_count": 1}
        assert "PRIVATE_INPUT" not in response.text and "PRIVATE_MODEL_REASON" not in response.text
        assert "键盘" not in response.text and "456789" not in response.text
        page = c.get(url + "/trace", params={"limit": 2}).json()
        assert page["has_more"] and len(page["events"]) == 2
        tail = c.get(url + "/trace", params={"after": page["next_after"]}).json()
        assert page["events"] + tail["events"] == events
    executor.assert_called_once()


PLAN = {"metric": "paid", "dimension": "day", "start": "2026-09-01",
        "end": "2026-09-30", "end_inclusive": True}


def test_cancel_never_emits_sql_events_and_unknown_is_404():
    app, executor = _setup()
    with app() as c:
        assert c.get("/sessions/bad-id/trace").status_code == 404
        pending = c.post("/sessions", json={"plan": PLAN}).json()
        url = f"/sessions/{pending['id']}"
        assert c.post(url + "/respond", json={"revision": 0, "action": "cancel"}).status_code == 200
        names = [event["name"] for event in c.get(url + "/trace").json()["events"]]
        assert names[-1] == "session_cancelled"
        assert not any(name.startswith("sql_") for name in names)
    executor.assert_not_called()


def test_sql_error_records_safe_failure():
    app, _ = _setup(executor=Mock(side_effect=QueryExecutionError("PRIVATE_DSN")))
    with app() as c:
        pending = c.post("/sessions", json={"plan": PLAN}).json()
        url = f"/sessions/{pending['id']}"
        result = c.post(url + "/respond", json={"revision": 0, "action": "confirm"}).json()
        assert result["stage"] == "error"
        trace = c.get(url + "/trace")
        event = trace.json()["events"][-1]
        assert event["name"] == "sql_failed" and event["reason_code"] == "query_execution_failed"
        assert "PRIVATE_DSN" not in trace.text


def test_trace_storage_failure_never_blocks_queries():
    store = Mock()
    store.append.side_effect = ConnectionError("PRIVATE_DSN")
    store.read.side_effect = ConnectionError("PRIVATE_DSN")
    app, executor = _setup(store=store)
    with app() as c:
        pending = c.post("/sessions", json={"plan": PLAN}).json()
        url = f"/sessions/{pending['id']}"
        assert c.post(url + "/respond", json={"revision": 0, "action": "confirm"}).json()["stage"] == "done"
        response = c.get(url + "/trace")
        assert response.status_code == 503 and "PRIVATE_DSN" not in response.text
    executor.assert_called_once()


def test_evidence_diagnostics_do_not_block_a_valid_plan_or_expose_quotes():
    planner = Mock(return_value=Decision.model_validate({
        "reason": "candidate",
        "plan": PLAN,
        "evidence": {"dimension": [{"message": 1, "quote": "PRIVATE_WRONG_REFERENCE"}],
                     "date": [{"message": 0, "quote": "2026年9月"}]},
    }))
    executor = Mock(return_value=[["2026-09-01", 12]])
    store = MemoryEvents()
    service = SessionService(InMemorySaver(), load_catalog(), executor, Mock(return_value=True),
                             planner=planner, trace=TraceRecorder(store), evidence_diagnostics=True)
    with TestClient(create_app(probes={}, sessions=lambda: service)) as client:
        pending = client.post("/sessions", json={"question": "2026年9月，一天一天实际付了多少钱？"}).json()
        assert pending["stage"] == "confirm"
        assert pending["decision"]["evidence"]["dimension"][0]["quote"] == "PRIVATE_WRONG_REFERENCE"
        url = "/sessions/" + pending["id"]
        trace = client.get(url + "/trace")
        names = [event["name"] for event in trace.json()["events"]]
        summary = next(event["summary"] for event in trace.json()["events"]
                       if event["name"] == "evidence_diagnostics")
        assert summary["missing_citations"] == ["metric"]
        assert summary["invalid_citations"] == ["dimension"]
        assert "PRIVATE_WRONG_REFERENCE" not in trace.text
        assert "sql_started" not in names
        executor.assert_not_called()

        done = client.post(url + "/respond", json={"action": "confirm", "revision": 0}).json()
        assert done["stage"] == "done" and done["executions"] == 1
    executor.assert_called_once()


def test_diagnostics_keeps_unambiguous_user_date_and_rejects_missing_snapshot():
    planner = Mock(return_value=Decision.model_validate({
        "reason": "candidate",
        "plan": {"metric": "stock_qty", "dimension": "category",
                 "start": "2026-10-31", "end": "2026-10-31", "end_inclusive": True},
    }))
    executor = Mock()
    store = MemoryEvents()
    service = SessionService(InMemorySaver(), load_catalog(), executor, Mock(return_value=True),
                             planner=planner, trace=TraceRecorder(store), evidence_diagnostics=True)
    with TestClient(create_app(probes={}, sessions=lambda: service)) as client:
        response = client.post("/sessions", json={"question": "2026年9月30日的库存按品类看。"}).json()
        assert response["stage"] == "reject"
        assert response["decision"]["plan"]["start"] == "2026-09-30"
        url = "/sessions/" + response["id"]
        events = client.get(url + "/trace").json()["events"]
        assert "explicit_date_applied" in [event["name"] for event in events]
        assert client.post(url + "/respond", json={"action": "confirm", "revision": 0}).status_code == 409
    executor.assert_not_called()


def test_diagnostics_does_not_skip_requested_clarification():
    planner = Mock(return_value=Decision.model_validate({
        "status": "clarify", "reason": "请选择销售额口径",
        "plan": PLAN, "missing_fields": ["metric"],
    }))
    executor = Mock()
    service = SessionService(InMemorySaver(), load_catalog(), executor, Mock(return_value=True),
                             planner=planner, evidence_diagnostics=True)
    with TestClient(create_app(probes={}, sessions=lambda: service)) as client:
        pending = client.post("/sessions", json={"question": "2026年9月每天销售额是多少？"}).json()
        assert pending["stage"] == "clarify"
        assert pending["decision"]["missing_fields"] == ["metric"]
    executor.assert_not_called()


def test_explicit_date_does_not_treat_open_bounds_or_alternatives_as_one_day():
    from t2s.planning.dates import explicit_date_in_text

    assert explicit_date_in_text("从2026年9月30日开始查库存。") is None
    assert explicit_date_in_text("查2026年9月30日或10月31日的库存。") is None
    assert explicit_date_in_text("查2026年9月30日和上月的库存。") is None
    assert explicit_date_in_text("查截至2026年9月30日的库存。") is None
    assert explicit_date_in_text("查2026年9月30日的库存。").start.isoformat() == "2026-09-30"
