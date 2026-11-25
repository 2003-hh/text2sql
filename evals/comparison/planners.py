"""不调用模型的对照组，只使用课程词典、原话和日期规则。"""
import re

from t2s.main import create_app
from t2s.planning.dates import parse_date_range
from t2s.planning.evidence import explicit_metric
from t2s.planning.models import Decision, Evidence, PartialPlan
from t2s.settings import Settings


_DIMENSIONS = {
    "day": ("按天", "每天", "每日", "逐日"),
    "month": ("按月", "每月", "逐月"),
    "category": ("按品类", "按类别"),
}
_CATEGORIES = ("键盘", "耳机", "电脑")
_METRIC_CONFLICT = re.compile(r"不是|并非|不要按|不按|而非|或者|还是(?:按|用)?(?:实付|净额|退款)")
_DATE_MENTION = re.compile(r"\d{1,4}[年月日]|\d{4}-\d{2}-\d{2}|今天|昨天|上月|本月|这个月")


def build_rule_planner(_settings, catalog):
    metric_names = {
        key: (entry["name"], *entry["aliases"]) for key, entry in catalog["metrics"].items()
    }
    ambiguous = [term.casefold() for term in catalog["ambiguousTerms"]]

    def interpret(messages: list[str]) -> Decision:
        plan = {}
        evidence: dict[str, list[Evidence]] = {}
        for index, text in enumerate(messages):
            quote = [Evidence(message=index, quote=text)]
            named_metrics = {
                key for key, names in metric_names.items() if any(name in text for name in names)
            }
            explicit = explicit_metric(text)
            if explicit == "net":
                named_metrics.discard("paid")  # 净额的标准口径本身包含实付金额。
            if explicit is not None:
                named_metrics.add(explicit)
            if len(named_metrics) > 1 and re.search(r"同时|分别|一张.*结果", text):
                return Decision(status="reject", reason="一次查询只支持一个指标")
            if len(named_metrics) == 1 and not _METRIC_CONFLICT.search(text):
                plan["metric"] = next(iter(named_metrics))
                evidence["metric"] = quote
            elif named_metrics or any(term in text.casefold() for term in ambiguous):
                plan.pop("metric", None)
                evidence.pop("metric", None)

            if span := parse_date_range(text):
                plan.update(start=span.start, end=span.end, end_inclusive=False)
                evidence["date"] = quote
            elif _DATE_MENTION.search(text):
                plan.pop("start", None)
                plan.pop("end", None)
                evidence.pop("date", None)

            dimensions = [key for key, words in _DIMENSIONS.items() if any(word in text for word in words)]
            if len(dimensions) == 1:
                plan["dimension"] = dimensions[0]
                evidence["dimension"] = quote
            elif len(dimensions) > 1:
                plan.pop("dimension", None)
                evidence.pop("dimension", None)
            elif re.search(r"不分组|合计|总计", text):
                plan["dimension"] = "none"
                evidence["dimension"] = quote

            categories = [category for category in _CATEGORIES if category in text]
            if len(categories) == 1:
                plan["category"] = categories[0]
                evidence["category"] = quote
            elif len(categories) > 1:
                plan.pop("category", None)
                evidence["category"] = []

        missing = [field for field, key in (("metric", "metric"), ("date", "start"),
                                            ("dimension", "dimension")) if key not in plan]
        if evidence.get("category") == []:
            missing.append("category")
        return Decision(
            status="clarify" if missing else "query",
            reason="请明确查询条件" if missing else "已从原话提取查询条件",
            plan=PartialPlan(**plan), missing_fields=missing, evidence=evidence,
        )

    return interpret


def _evaluation_app(arm: str, **options):
    app = create_app(**options)
    app.add_api_route("/eval-planner", lambda: {"planner": arm}, methods=["GET"])
    # 匹配身份路径后再把其余网页请求交给根静态挂载。
    app.router.routes.insert(-1, app.router.routes.pop())
    return app


def rules_app():
    return _evaluation_app("rules", planner_factory=build_rule_planner)


def model_app():
    return _evaluation_app("model", settings=Settings(evidence_diagnostics=False))


def diagnostic_app():
    return _evaluation_app("diagnostic", settings=Settings(evidence_diagnostics=True))
