from datetime import date, timedelta

from t2s.planning.models import PartialPlan, Plan


def normalize_plan(plan: Plan, catalog: dict) -> Plan:
    metric = catalog["metrics"].get(plan.metric)
    if metric is None or plan.dimension not in metric["dimensions"]:
        raise ValueError("不支持该指标或分组")
    if plan.end_inclusive and plan.end == date.max:
        raise ValueError("结束日期超出可表示范围")
    end = plan.end + timedelta(days=1) if plan.end_inclusive else plan.end
    days = (end - plan.start).days
    if not 1 <= days <= 366:
        raise ValueError("日期范围须为1至366天")
    if plan.category is not None and (not plan.category.strip() or not metric["categoryJoin"]):
        raise ValueError("不支持该品类筛选")
    snapshot = metric["snapshotDate"]
    if snapshot is not None and (plan.start.isoformat() != snapshot or days != 1):
        raise ValueError("该日期没有受支持的库存快照")
    return plan.model_copy(update={"end": end, "end_inclusive": False})


def required_tables(plan: PartialPlan, catalog: dict) -> list[str]:
    tables = set(catalog["metrics"][plan.metric]["tables"])
    if plan.category is not None or plan.dimension == "category":
        tables.add("products")
        if plan.metric != "stock_qty":
            tables.add("order_items")
    return sorted(tables)
