"""在真实服务上验证会话的 Trace。pause 和 finish 之间重启 FastAPI。"""

import argparse
import json
from pathlib import Path

import httpx


def events(client: httpx.Client, url: str) -> list[dict]:
    return client.get(url + "/trace").raise_for_status().json()["events"]


def pause(client: httpx.Client) -> dict:
    question = "每天键盘实付金额是多少？"
    initial = client.post("/sessions", json={"question": question}).raise_for_status().json()
    assert initial["stage"] == "clarify", initial["reason"]
    assert initial["decision"]["missing_fields"] == ["date"]
    url = "/sessions/" + initial["id"]
    chosen = client.post(url + "/respond", json={
        "revision": 0, "action": "choose",
        "choice": {"start": "2026-09-01", "end": "2026-10-01"},
    }).raise_for_status().json()
    assert (chosen["stage"], chosen["revision"], chosen["executions"]) == ("confirm", 1, 0)
    trace = events(client, url)
    names = [event["name"] for event in trace]
    assert names == ["session_created", "planner_started", "planner_finished",
                     "plan_validated", "clarification_required", "workflow_resumed",
                     "choice_applied", "plan_validated", "approval_required"], names
    encoded = json.dumps(trace, ensure_ascii=False)
    assert all(value not in encoded for value in (question, "键盘", "2026-09-01"))
    assert len({event["request_id"] for event in trace}) == 2
    return {"session_id": initial["id"], "before_confirm": trace}


def finish(client: httpx.Client, result: dict) -> None:
    url = "/sessions/" + result["session_id"]
    assert events(client, url) == result["before_confirm"]
    restored = client.get(url).raise_for_status().json()
    assert (restored["stage"], restored["revision"], restored["executions"]) == ("confirm", 1, 0)
    assert client.post(url + "/respond", json={"revision": 0, "action": "confirm"}).status_code == 409
    done = client.post(url + "/respond", json={
        "revision": 1, "action": "confirm",
    }).raise_for_status().json()
    assert (done["stage"], done["revision"], done["executions"]) == ("done", 2, 1)
    assert client.post(url + "/respond", json={"revision": 1, "action": "confirm"}).status_code == 409
    trace = events(client, url)
    names = [event["name"] for event in trace]
    assert names.count("revision_conflict") == 2
    assert names.count("sql_started") == names.count("sql_finished") == 1
    started, finished = [item for item in trace if item["category"] == "sql"]
    assert started["request_id"] == finished["request_id"]
    assert started["revision"] == finished["revision"] == 2
    assert finished["summary"]["row_count"] == len(done["rows"]) == 30
    page = client.get(url + "/trace", params={"limit": 3}).raise_for_status().json()
    remainder = client.get(url + "/trace", params={"after": page["next_after"]})
    assert page["has_more"] and page["events"] + remainder.json()["events"] == trace

    cancel = client.post("/sessions", json={"plan": {
        "metric": "paid", "dimension": "day", "start": "2026-09-01",
        "end": "2026-09-30", "end_inclusive": True,
    }}).raise_for_status().json()
    cancel_url = "/sessions/" + cancel["id"]
    cancelled = client.post(cancel_url + "/respond", json={"revision": 0, "action": "cancel"})
    assert cancelled.raise_for_status().json()["executions"] == 0
    cancelled_trace = events(client, cancel_url)
    assert cancelled_trace[-1]["name"] == "session_cancelled"
    assert not any(item["name"].startswith("sql_") for item in cancelled_trace)
    assert client.get("/sessions/00000000-0000-0000-0000-000000000000/trace").status_code == 404
    result.update({"after_confirm": trace, "cancelled": cancelled_trace, "recovered": True})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "finish"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18330")
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=115) as client:
        if args.action == "pause":
            result = pause(client)
            print("Trace matches natural question and choice; SQL calls=0")
        else:
            result = json.loads(args.record.read_text(encoding="utf-8"))
            finish(client, result)
            print("Trace survives restart; SQL calls=1; stale approvals blocked; cancel executes nothing")
    args.record.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
