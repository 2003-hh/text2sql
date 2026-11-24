from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from t2s.infrastructure.catalog import load_catalog
from t2s.main import create_app
from t2s.sessions.service import SessionService

PLAN = {"metric": "paid", "dimension": "day",
        "start": "2026-09-01", "end": "2026-09-30", "end_inclusive": True}


def test_wait_restore_confirm_once():
    saver = InMemorySaver()
    executor = Mock(return_value=[["2026-09-01", 100]])
    claim = Mock(return_value=True)

    def app():
        service = SessionService(saver, load_catalog(), executor, claim)
        return create_app(probes={}, sessions=lambda: service)

    with TestClient(app()) as client:
        response = client.post("/sessions", json={"plan": PLAN})
        assert response.status_code == 200
        pending = response.json()
        assert (pending["stage"], pending["revision"], pending["executions"]) == ("confirm", 0, 0)
        assert pending["rows"] == []
        assert pending["decision"]["plan"]["end"] == "2026-10-01"
        executor.assert_not_called()
        claim.assert_not_called()

    url = f"/sessions/{pending['id']}"
    with TestClient(app()) as client:
        assert client.get(url).json() == pending
        assert client.post(url + "/respond", json={"revision": 1, "action": "confirm"}).status_code == 409
        executor.assert_not_called()

        def confirm():
            return client.post(url + "/respond", json={"revision": 0, "action": "confirm"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm(), range(2)))
        assert sorted(response.status_code for response in results) == [200, 409]
        done = client.get(url).json()
        assert (done["stage"], done["revision"], done["executions"]) == ("done", 1, 1)
        assert done["rows"] == [["2026-09-01", 100]]
        executor.assert_called_once()
        claim.assert_called_once_with(pending["id"], 1)


def test_cancel_and_invalid_plan_never_query():
    executor = Mock()
    claim = Mock()
    service = SessionService(InMemorySaver(), load_catalog(), executor, claim)
    with TestClient(create_app(probes={}, sessions=lambda: service)) as client:
        bad = client.post("/sessions", json={"plan": {**PLAN, "metric": "unknown"}})
        assert bad.status_code == 400
        pending = client.post("/sessions", json={"plan": PLAN}).json()
        result = client.post(f"/sessions/{pending['id']}/respond",
                             json={"revision": 0, "action": "cancel"})
        assert result.json()["stage"] == "cancelled"
        assert result.json()["executions"] == 0
    executor.assert_not_called()
    claim.assert_not_called()


def test_already_claimed_execution_does_not_query_again():
    executor = Mock()
    service = SessionService(InMemorySaver(), load_catalog(), executor, Mock(return_value=False))
    with TestClient(create_app(probes={}, sessions=lambda: service)) as client:
        pending = client.post("/sessions", json={"plan": PLAN}).json()
        result = client.post(f"/sessions/{pending['id']}/respond",
                             json={"revision": 0, "action": "confirm"})
        assert result.json()["stage"] == "error"
        assert result.json()["executions"] == 0
    executor.assert_not_called()
