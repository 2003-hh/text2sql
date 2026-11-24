"""用真实会话接口、结构化轨迹和独立 SQL 评测 T2S。"""

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import httpx
from pymysql import MySQLError

from evals.scoring import compare_plan, trace_findings
from t2s.infrastructure.mysql import connect_mysql
from t2s.settings import Settings


HERE = Path(__file__).resolve().parent
COURSE_SQL = HERE / "course-reference.sql"
GROUPS = {"query": "直接查询", "clarify": "澄清与多轮", "reject": "超范围拒绝"}
CANCEL_CASE = {
    "id": "S01", "group": "safety",
    "plan": {"metric": "paid", "dimension": "day", "start": "2026-09-01",
             "end": "2026-09-30", "end_inclusive": True},
    "turns": [
        {"expect": {"stage": "confirm"}},
        {"action": "cancel", "expect": {"stage": "cancelled"}},
    ],
}


def load_references(needed: set[str], settings: Settings) -> tuple[dict, dict]:
    rows, errors = {}, {}
    sources = {
        COURSE_SQL: ("A01", "A02", "A03"),
        HERE / "references.sql": ("R01", "R02"),
    }
    for path, labels in sources.items():
        selected = needed.intersection(labels)
        if not selected:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            sql = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
            statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
            commands = dict(zip(labels, statements, strict=True))
        except (OSError, ValueError) as error:
            errors.update(dict.fromkeys(selected, f"参考 SQL 文件无法读取或结构不符（{type(error).__name__}）"))
            continue
        for label in sorted(selected):
            try:
                with closing(connect_mysql(settings)) as connection, connection.cursor() as cursor:
                    cursor.execute("SET SESSION MAX_EXECUTION_TIME=1500")
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute(commands[label])
                    rows[label] = [
                        [key.isoformat() if isinstance(key, date) else key,
                         int(value) if isinstance(value, Decimal) and value == value.to_integral_value() else value]
                        for key, value in cursor.fetchall()
                    ]
            except MySQLError as error:
                errors[label] = f"独立参考查询失败（{type(error).__name__}）"
    return rows, errors


def get_trace(client: httpx.Client, url: str) -> list[dict]:
    path = url + "/trace"
    events, after = [], 0
    while True:
        page = client.get(path, params={"after": after, "limit": 200}).raise_for_status().json()
        events.extend(page["events"])
        if page["history_trimmed"]:
            raise ValueError("轨迹已裁剪")
        if not page["has_more"]:
            break
        if page["next_after"] <= after:
            raise ValueError("轨迹游标未推进")
        after = page["next_after"]
    if not events or events[0]["name"] != "session_created":
        raise ValueError("缺少会话创建事件")
    return events


def check_turn(result: dict, expected: dict, session: dict) -> dict:
    decision = session.get("decision") or {}
    actual = {"stage": session["stage"], "executions": session["executions"],
              "revision": session["revision"], "plan": decision.get("plan"),
              "missing_fields": decision.get("missing_fields"), "reason": session.get("reason")}
    observed = {"expected": expected, "actual": actual}
    issues = result["issues"]
    if actual["stage"] == "error":
        result["errors"].append(actual["reason"] or "会话返回 error")
        return observed
    if actual["stage"] != expected["stage"]:
        issues.append(f"阶段预期 {expected['stage']}，实际 {actual['stage']}")
    if "plan" in expected:
        if actual["plan"] is None:
            issues.append("缺少可比较的查询计划")
        else:
            checked = compare_plan(expected["plan"], actual["plan"])
            observed["plan_checks"] = checked
            issues.extend(f"计划字段 {field} 不符" for field, passed in checked.items() if not passed)
    if "missing_fields" in expected and set(actual["missing_fields"] or []) != set(expected["missing_fields"]):
        issues.append(f"应澄清 {expected['missing_fields']}，实际 {actual['missing_fields']}")
    return observed


def compare_rows(result: dict, session: dict, reference: str, rows: dict, errors: dict) -> None:
    if reference in errors:
        result["errors"].append(errors[reference])
        return
    expected = rows[reference]
    actual = session["rows"]
    result["rows"] = {"expected_count": len(expected), "actual_count": len(actual)}
    if len(expected) != len(actual):
        result["issues"].append("结果行数不同")
    for index, (want, got) in enumerate(zip(expected, actual), start=1):
        if want != got:
            result["rows"]["first_difference"] = {"row": index, "expected": want, "actual": got}
            result["issues"].append(f"第 {index} 行分组或数值不一致")
            break


def run_case(client: httpx.Client, case: dict, reference_rows: dict, reference_errors: dict) -> dict:
    result = {"id": case["id"], "group": case["group"], "question": case.get("question"),
              "turns": [], "issues": [], "errors": [], "violations": []}
    started = perf_counter()
    approval_sent = False
    session, url = None, None
    try:
        payload = {"question": case["question"]} if "question" in case else {"plan": case["plan"]}
        session = client.post("/sessions", json=payload).raise_for_status().json()
        url = "/sessions/" + session["id"]
        result["trace_url"] = str(client.base_url.join(url + "/trace"))
        for turn in case["turns"]:
            action = turn.get("action")
            if action:
                payload = {key: turn[key] for key in ("action", "text") if key in turn}
                payload["revision"] = session["revision"]
                approval_sent = action == "confirm"
                session = client.post(url + "/respond", json=payload).raise_for_status().json()
            if not approval_sent and session["executions"] != 0:
                result["violations"].append(f"批准前已报告 {session['executions']} 次成功执行")
            if action == "confirm" and session["stage"] == "done" and session["executions"] != 1:
                result["violations"].append(f"已完成会话报告 {session['executions']} 次成功执行")
            observed = check_turn(result, turn["expect"], session)
            observed["action"] = action or "ask"
            result["turns"].append(observed)
            if result["issues"] or result["errors"] or result["violations"]:
                break
            if action == "confirm":
                compare_rows(result, session, case["reference"], reference_rows, reference_errors)
                duplicate = client.post(url + "/respond", json=payload)
                if duplicate.status_code != 409:
                    if duplicate.status_code >= 500:
                        duplicate.raise_for_status()
                    result["violations"].append(f"重复提交预期返回 409，实际 {duplicate.status_code}")
    except httpx.HTTPError as error:
        status = f" HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else ""
        result["errors"].append(f"接口请求失败（{type(error).__name__}{status}）")

    if url:
        try:
            events = get_trace(client, url)
            result["violations"].extend(trace_findings(events, approval_sent=approval_sent))
            result["sql_starts"] = sum(event["name"] == "sql_started" for event in events)
            current = result["turns"][-1]["actual"] if result["turns"] else {}
            names = {event["name"] for event in events}
            expected_terminal = {"confirm": "approval_required", "clarify": "clarification_required",
                                 "reject": "plan_rejected", "cancelled": "session_cancelled",
                                 "done": "sql_finished"}.get(current.get("stage"))
            if expected_terminal and expected_terminal not in names:
                raise ValueError("缺少 " + expected_terminal)
            if current.get("stage") == "done" and result["sql_starts"] == 0:
                raise ValueError("缺少成功查询对应的 sql_started")
        except (httpx.HTTPError, ValueError) as error:
            note = str(error) if isinstance(error, ValueError) else type(error).__name__
            result["errors"].append(f"无法验证轨迹（{note}）")
    result["status"] = (
        "violation" if result["violations"] else "error" if result["errors"] else
        "fail" if result["issues"] else "pass"
    )
    result["duration_ms"] = round((perf_counter() - started) * 1000)
    return result


def markdown(results: list[dict], cases: list[dict], metadata: dict) -> str:
    lines = ["# T2S Eval 基线报告", "", f"运行时间 {metadata['timestamp']}（UTC）",
             f"本地配置的 Ollama 模型 `{metadata['model']}`",
             f"题集 SHA-256 `{metadata['dataset_sha256']}`", "",
             "| 题型 | 通过 | 内容失败 | 运行错误 | 违规 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for group, name in GROUPS.items():
        group_results = [item for item in results if item["group"] == group]
        if group_results:
            counts = Counter(item["status"] for item in group_results)
            lines.append(f"| {name} | {counts['pass']}/{len(group_results)} | "
                         f"{counts['fail']} | {counts['error']} | {counts['violation']} |")

    checked = [turn["plan_checks"] for item in results for turn in item["turns"] if "plan_checks" in turn]
    expected = sum("plan" in turn["expect"] for case in cases for turn in case["turns"])
    violations = sum(len(item["violations"]) for item in results)
    trace_count = sum("sql_starts" in item and not any(
        issue.startswith("无法验证轨迹") for issue in item["errors"]
    ) for item in results)
    lines.extend(["", f"计划可比对 {len(checked)}/{expected} 次。"])
    for field in ("metric", "date", "dimension", "category"):
        passed = sum(check[field] for check in checked)
        lines.append(f"- `{field}` {passed}/{len(checked)}" if checked else f"- `{field}` 无可比对计划")
    lines.extend([
        "", f"违规发现 {violations} 条。轨迹检查覆盖 {trace_count}/{len(results)} 个会话（含取消对照）。",
        "", "## 逐题结果", "",
    ])
    labels = {"pass": "通过", "fail": "内容失败", "error": "运行错误", "violation": "违规"}
    for item in results:
        link = f" · [Trace]({item['trace_url']})" if "trace_url" in item else ""
        question = f" · {item['question']}" if item["question"] else " · 固定计划取消对照"
        lines.append(f"- **{item['id']} {labels[item['status']]}**{question}{link}")
        lines.extend(f"  - {note}" for note in item["issues"] + item["errors"] + item["violations"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18330")
    parser.add_argument("--output", type=Path, default=HERE.parent / ".run/evals/latest")
    parser.add_argument("--case", action="append", help="固定题号，可重复传入")
    args = parser.parse_args()

    dataset = (HERE / "cases.json").read_bytes()
    cases = json.loads(dataset)
    if args.case:
        unknown = set(args.case) - {item["id"] for item in cases}
        if unknown:
            parser.error(f"未知题号 {', '.join(sorted(unknown))}")
        cases = [item for item in cases if item["id"] in args.case]
    settings = Settings()
    metadata = {"timestamp": datetime.now(timezone.utc).isoformat(), "model": settings.ollama_model,
                "dataset_sha256": sha256(dataset).hexdigest()}
    references = {item["reference"] for item in cases if "reference" in item}
    results = []
    with httpx.Client(base_url=args.base_url, timeout=settings.ollama_timeout + 30) as client:
        try:
            client.get("/ready", timeout=10).raise_for_status()
        except httpx.HTTPError as error:
            print(f"T2S 服务未就绪（{type(error).__name__}）")
            return 2
        rows, errors = load_references(references, settings)
        for case in [*cases, CANCEL_CASE]:
            result = run_case(client, case, rows, errors)
            results.append(result)
            print(f"{case['id']}: {result['status']} ({result['duration_ms']} ms)", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps({"run": metadata, "cases": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "report.md").write_text(markdown(results, cases, metadata), encoding="utf-8")
    print(f"Report: {args.output.resolve()}")
    if any(result["errors"] for result in results):
        return 2
    return int(any(result["status"] != "pass" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
