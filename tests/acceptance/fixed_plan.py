"""在暂停和完成之间重启 FastAPI，验证原会话可恢复并核对独立 SQL。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path

import httpx

from t2s.infrastructure.mysql import connect_mysql
from t2s.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
PLANS = (
    {"metric": "paid", "dimension": "day", "start": "2026-09-01", "end": "2026-09-30"},
    {"metric": "count", "dimension": "month", "start": "2026-07-01", "end": "2026-09-30"},
    {"metric": "stock_qty", "dimension": "category", "start": "2026-10-31", "end": "2026-10-31"},
)


def expected_rows() -> list:
    path = ROOT / "evals/course-reference.sql"
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))
    statements = [statement.strip() for statement in text.split(";") if statement.strip()]
    assert len(statements) == len(PLANS)
    with closing(connect_mysql(Settings())) as connection, connection.cursor() as cursor:
        rows = []
        for sql in statements:
            cursor.execute(sql)
            rows.append([[str(key), int(value)] for key, value in cursor.fetchall()])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "finish"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18330")
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=20) as client:
        assert client.get("/ready").raise_for_status().json()["status"] == "ready"
        if args.action == "pause":
            pending = []
            for plan in PLANS:
                session = client.post("/sessions", json={
                    "plan": {**plan, "end_inclusive": True},
                }).raise_for_status().json()
                assert (session["stage"], session["revision"], session["executions"]) == (
                    "confirm", 0, 0,
                )
                assert session["rows"] == []
                pending.append(session)
            args.record.write_text(json.dumps({"pending": pending}, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
            print("A01-A03 created and waiting: executions=0")
            return

        record = json.loads(args.record.read_text(encoding="utf-8"))
        checks = []
        for index, (pending, expected) in enumerate(
                zip(record["pending"], expected_rows(), strict=True), start=1):
            url = f"/sessions/{pending['id']}"
            assert client.get(url).raise_for_status().json() == pending
            assert client.post(url + "/respond", json={
                "action": "confirm", "revision": 1,
            }).status_code == 409

            def confirm():
                return client.post(url + "/respond", json={"action": "confirm", "revision": 0})

            with ThreadPoolExecutor(max_workers=2) as pool:
                replies = list(pool.map(lambda _: confirm(), range(2)))
            statuses = sorted(r.status_code for r in replies)
            assert statuses == [200, 409], [r.text for r in replies]
            done = client.get(url).raise_for_status().json()
            assert (done["stage"], done["executions"]) == ("done", 1), done["reason"]
            assert done["rows"] == expected
            checks.append({
                "case": f"A{index:02}", "restored": True, "statuses": statuses,
                "executions": done["executions"], "exact_match": True,
                "row_count": len(expected), "rows": done["rows"], "reference_rows": expected,
            })
            print(f"A{index:02}: {len(expected)} rows match; restored; confirm 200/409")
        args.record.write_text(json.dumps({**record, "checks": checks}, indent=2, ensure_ascii=False),
                               encoding="utf-8")


if __name__ == "__main__":
    main()
