import re

from t2s.planning.dates import DateRange, parse_date_range
from t2s.planning.models import Evidence, Plan


def valid_quotes(messages: list[str], evidence: list[Evidence]) -> bool:
    return bool(evidence) and all(
        item.message < len(messages) and item.quote.strip()
        and item.quote in messages[item.message]
        for item in evidence
    )


def explicit_metric(text: str) -> str | None:
    paid = bool(re.search("支付金额|实付|支付总额", text))
    count = bool(re.search("订单数|订单.*(?:计数|数量)|多少单", text))
    if paid and "退款" in text and re.search("减|扣", text) and "不扣退款" not in text:
        if re.search("全部|所有", text) and re.search("完成|completed", text) and re.search(
            "这些订单|该批订单|各自|该批", text
        ):
            return "net"
        return None
    if re.search("支付|付款", text) and count and not paid:
        return "count"
    if paid:
        return "paid"
    if "已发货订单数" in text:
        return "shipped_orders"
    if "库存余额" in text:
        return "stock_qty"
    return None


def resolve_metric_reply(text: str, catalog: dict) -> str | None:
    """完整回复必须能归到目录中的唯一指标，附带其他条件时交回规划器。"""
    value = text.strip().rstrip("。.!！").strip()
    if definition := re.fullmatch(r"(.+?)(?:这里)?(?:是指|指的是|指|就是|是)\s*(.+)", value):
        term, value = definition.groups()
        if term.strip().casefold() not in {term.casefold() for term in catalog["ambiguousTerms"]}:
            return None
    elif choice := re.fullmatch(r"按\s*(.+?)\s*(?:算|统计)", value):
        value = choice[1]

    alias = value.strip().casefold()
    matches = [
        metric for metric, entry in catalog["metrics"].items()
        if alias in {name.casefold() for name in [entry["name"], *entry["aliases"]]}
    ]
    if len(matches) != 1:
        return None
    metric = matches[0]
    evidence = [Evidence(message=0, quote=text)]
    return metric if metric_supported([text], evidence, metric, catalog["ambiguousTerms"]) else None


def metric_supported(messages: list[str], evidence: list[Evidence], metric: str, terms: list[str]) -> bool:
    if not valid_quotes(messages, evidence):
        return False
    for index in range(len(messages) - 1, -1, -1):
        text = messages[index]
        explicit = explicit_metric(text)
        ambiguous = any(term.casefold() in text.casefold() for term in terms)
        if not ambiguous and explicit is None and not re.search("实付.*(?:扣|减).*退款", text):
            continue
        if re.search(
            r"不是|并非|不要按|不按|而非|或者|"
            r"(?:还是|或是?)\s*(?:按|用)?(?:支付|实付|退款|净额|已发货|库存|订单)", text
        ):
            return False
        return explicit == metric and any(item.message == index for item in evidence)
    return False


def date_supported(messages: list[str], evidence: list[Evidence], plan: Plan) -> bool:
    if not valid_quotes(messages, evidence):
        return False
    latest = next(
        ((i, span) for i in range(len(messages) - 1, -1, -1)
         if (span := parse_date_range(messages[i])) is not None),
        None,
    )
    expected = DateRange(plan.start, plan.end)
    if latest is None or latest[1] != expected:
        return False
    # 引文自身应能解析，且必须来自最近提供该日期的用户消息。
    return any(item.message == latest[0] and parse_date_range(item.quote) == expected for item in evidence)
