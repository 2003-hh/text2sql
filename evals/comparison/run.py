"""对照评测，同一题交替发送给两个隔离的 T2S 服务。"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

import httpx

from evals.run import CANCEL_CASE, load_references, run_case
from t2s.settings import Settings


HERE = Path(__file__).resolve().parent
GROUPS = {"explicit": "常规", "colloquial": "口语省略", "multi": "多轮", "boundary": "含糊或超范围"}
ARMS = ("rules", "model")
LABELS = {"rules": "规则", "model": "模型（默认校验）", "diagnostic": "模型（引文仅诊断）"}


def outcome(result: dict) -> dict:
    wrong_card = False
    extra_questions = 0
    elapsed = 0
    last_card = None
    for turn in result["turns"]:
        if turn["action"] == "confirm":
            continue
        elapsed += turn["response_ms"]
        actual, expected = turn["actual"], turn["expected"]
        if actual["stage"] == "confirm":
            checks = turn.get("plan_checks", {})
            if expected["stage"] != "confirm" or not checks or not all(checks.values()):
                wrong_card = True
            else:
                last_card = elapsed
        if actual["stage"] == "clarify" and (
            expected["stage"] != "clarify"
            or set(actual["missing_fields"] or []) - set(expected.get("missing_fields", []))
        ):
            extra_questions += 1
    return {"wrong_card": wrong_card, "extra_questions": extra_questions,
            "last_card_ms": last_card if result["status"] == "pass" else None}


def report(header: dict, paired: list[dict], controls: dict, arms: tuple[str, str]) -> str:
    lines = [
        "# T2S 引文拦截与诊断对照" if "diagnostic" in arms else "# T2S 规则与模型对照", "",
        f"模型 `{header['model']}`，题集 SHA-256 `{header['dataset_sha256']}`。",
        "",
        f"| 题组 | {LABELS[arms[0]]}通过 | {LABELS[arms[1]]}通过 |",
        "| --- | ---: | ---: |",
    ]
    for group, label in GROUPS.items():
        items = [item for item in paired if item["group"] == group]
        counts = [sum(item[arm]["status"] == "pass" for item in items) for arm in arms]
        lines.append(f"| {label} | {counts[0]}/{len(items)} | {counts[1]}/{len(items)} |")

    shared = [item for item in paired if all(item[arm]["last_card_ms"] is not None for arm in arms)]
    lines += ["", f"耗时只比较两组都通过且生成确认卡的 {len(shared)} 道题。"
              "每题累计提交问题和补充说明的 HTTP 响应耗时，排除人工等待、确认执行和获取 Trace。", ""]
    for arm in arms:
        items = [item[arm] for item in paired]
        wrong = sum(item["wrong_card"] for item in items)
        extra = sum(item["extra_questions"] for item in items)
        errors = sum(bool(item["errors"]) for item in items)
        violations = sum(len(item["violations"]) for item in [*items, controls[arm]])
        timing = median(item[arm]["last_card_ms"] for item in shared) if shared else "无"
        lines.append(
            f"- {arm} 通过 {sum(item['status'] == 'pass' for item in items)} 道，"
            f"错误确认卡 {wrong} 道，额外追问 {extra} 轮，运行错误 {errors} 道，"
            f"违规 {violations} 条，取消对照 {controls[arm]['status']}，确认卡中位耗时 {timing} ms。"
        )
    for arm, other in (arms, arms[::-1]):
        ids = [item["id"] for item in paired if item[arm]["status"] == "pass" and item[other]["status"] != "pass"]
        lines.append(f"- 仅 {arm} 通过的题目 {', '.join(ids) or '无'}。")
    lines.extend(["", "## 逐题结果", ""])
    for item in paired:
        lines.append(f"- **{item['id']}** {item['question']}")
        for arm in arms:
            result = item[arm]
            issues = "；".join(result["issues"] + result["errors"] + result["violations"])
            link = f" [Trace]({result['trace_url']})" if "trace_url" in result else ""
            lines.append(f"  - {arm} {result['status']}{'，' + issues if issues else ''}。{link}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-url", default="http://127.0.0.1:18331")
    parser.add_argument("--model-url", default="http://127.0.0.1:18332")
    parser.add_argument("--diagnostic-url", help="与默认校验对照，使用同一个模型")
    parser.add_argument("--case", action="append", help="限定题号，可重复传入")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    raw = (HERE / "cases.json").read_bytes()
    cases = json.loads(raw)
    if args.case:
        unknown = set(args.case) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"未知题号 {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in args.case]
    diagnostic = bool(args.diagnostic_url)
    arms = ("model", "diagnostic") if diagnostic else ARMS
    output = args.output or (
        HERE.parent.parent / ".run/evals" / ("evidence-diagnostics" if diagnostic else "rule-vs-model")
    )
    settings = Settings()
    header = {"model": settings.ollama_model, "dataset_sha256": sha256(raw).hexdigest(),
              "timestamp": datetime.now(timezone.utc).isoformat()}
    labels = {case["reference"] for case in cases if "reference" in case}
    rows, errors = load_references(
        labels, settings, extra_sources={HERE / "references.sql": ("P01", "P02")},
    )
    if errors or labels - rows.keys():
        print(f"Independent references unavailable: {errors or labels - rows.keys()}")
        return 2
    paired = []
    with (
        httpx.Client(base_url=args.diagnostic_url or args.rules_url, timeout=settings.ollama_timeout + 30) as other,
        httpx.Client(base_url=args.model_url, timeout=settings.ollama_timeout + 30) as model,
    ):
        clients = {"model": model, "diagnostic" if diagnostic else "rules": other}
        try:
            for arm, client in clients.items():
                client.get("/ready").raise_for_status()
                if client.get("/eval-planner").raise_for_status().json().get("planner") != arm:
                    raise ValueError(f"Wrong evaluation service for {arm}")
        except (httpx.HTTPError, ValueError) as error:
            print(f"Evaluation service not ready: {error}")
            return 2
        for index, case in enumerate(cases):
            item = {key: case[key] for key in ("id", "group", "question")}
            for arm in (arms if index % 2 == 0 else arms[::-1]):
                result = run_case(clients[arm], case, rows, errors, include_evidence=diagnostic)
                item[arm] = {**result, **outcome(result)}
            paired.append(item)
            statuses = " ".join(f"{arm}={item[arm]['status']}" for arm in arms)
            print(f"{case['id']}: {statuses}", flush=True)
        controls = {arm: run_case(client, CANCEL_CASE, rows, errors) for arm, client in clients.items()}

    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps({"run": header, "pairs": paired, "controls": controls}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "report.md").write_text(report(header, paired, controls, arms), encoding="utf-8")
    print(f"Report: {output.resolve()}")
    results = [item[arm] for item in paired for arm in arms] + list(controls.values())
    if any(item["errors"] for item in results):
        return 2
    return int(any(item["violations"] for item in results) or
               any(item["status"] != "pass" for item in controls.values()))


if __name__ == "__main__":
    raise SystemExit(main())
