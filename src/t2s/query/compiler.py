from dataclasses import dataclass
from datetime import date

from t2s.planning.models import Plan
from t2s.planning.rules import normalize_plan


@dataclass(frozen=True)
class CompiledQuery:
    text: str
    params: tuple[date | str, ...]


def compile_sql(plan: Plan, catalog: dict) -> CompiledQuery:
    plan = normalize_plan(plan, catalog)
    metric = catalog["metrics"][plan.metric]
    source = metric["source"]
    value = metric["expression"]
    params: list[date | str] = [plan.start, plan.end]
    if plan.category is not None or plan.dimension == "category":
        source += metric["categoryJoin"]
        value = metric["categoryExpression"]

    time_column = metric["timeColumn"]
    # PyMySQL 参数替换时 %% 表示一个字面量 %。
    grouping = {
        "day": f"DATE_FORMAT({time_column}, '%%Y-%%m-%%d')",
        "month": f"DATE_FORMAT({time_column}, '%%Y-%%m')",
        "category": "p.category",
        "none": None,
    }[plan.dimension]
    where = f"{metric['condition']} AND {time_column} >= %s AND {time_column} < %s"
    if plan.category is not None:
        where += " AND p.category = %s"
        params.append(plan.category)
    columns = f"{grouping} AS dimension, " if grouping else ""
    group_by = " GROUP BY dimension ORDER BY dimension" if grouping else ""
    text = f"SELECT {columns}{value} AS value FROM {source} WHERE {where}{group_by} LIMIT 1001"
    return CompiledQuery(text, tuple(params))
