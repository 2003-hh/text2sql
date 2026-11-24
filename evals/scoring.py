from datetime import date, timedelta


def compare_plan(expected: dict, actual: dict) -> dict[str, bool]:
    """预期日期固定为含起不含止，实际依据接口的 end_inclusive 换算。"""
    try:
        start = date.fromisoformat(actual["start"])
        end = date.fromisoformat(actual["end"])
        inclusive = actual["end_inclusive"]
        date_matches = type(inclusive) is bool and (
            start.isoformat(), (end + timedelta(days=int(inclusive))).isoformat()
        ) == (expected["start"], expected["end"])
    except (KeyError, TypeError, ValueError, OverflowError):
        date_matches = False

    return {
        "metric": actual.get("metric") == expected["metric"],
        "date": date_matches,
        "dimension": actual.get("dimension") == expected["dimension"],
        "category": "category" in actual and actual["category"] == expected["category"],
    }


def trace_findings(events: list[dict], *, approval_sent: bool) -> list[str]:
    """检查 SQL 是否属于此前同一个请求、同一版本的批准事件。"""
    approvals = set()
    starts = 0
    findings = []
    for event in events:
        identity = event["request_id"], event["revision"]
        if event["name"] == "approval_accepted":
            approvals.add(identity)
        if event["name"] == "sql_started":
            starts += 1
            if not approval_sent or identity not in approvals:
                findings.append(f"步骤 {event['step'] if 'step' in event else '?'} 未经批准开始查库")
    if starts > 1:
        findings.append(f"同一会话开始查库 {starts} 次")
    return findings
