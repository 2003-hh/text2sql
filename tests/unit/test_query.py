from datetime import date

import pytest

from t2s.infrastructure.catalog import load_catalog
from t2s.planning.models import Plan
from t2s.query.compiler import compile_sql


@pytest.fixture
def catalog():
    return load_catalog()


def test_inclusive_end_and_category_binding(catalog):
    plan = Plan(metric="paid", start="2026-09-01", end="2026-09-30",
                end_inclusive=True, dimension="day", category="键盘")
    query = compile_sql(plan, catalog)
    assert query.params == (date(2026, 9, 1), date(2026, 10, 1), "键盘")
    assert "SUM(i.paid_cents)" in query.text and "p.category = %s" in query.text
    assert "键盘" not in query.text and "2026-09-01" not in query.text


@pytest.mark.parametrize("days, valid", [(366, True), (367, False)])
def test_date_span_boundary(catalog, days, valid):
    from datetime import timedelta

    start = date(2026, 1, 1)
    plan = Plan(metric="paid", start=start, end=start + timedelta(days=days),
                end_inclusive=False, dimension="none")
    if valid:
        assert compile_sql(plan, catalog).params == (plan.start, plan.end)
    else:
        with pytest.raises(ValueError, match="366"):
            compile_sql(plan, catalog)


def test_net_aggregates_before_join(catalog):
    query = compile_sql(Plan(metric="net", start="2026-09-01", end="2026-10-01",
                             end_inclusive=False, dimension="none"), catalog)
    assert "LEFT JOIN" in query.text and "GROUP BY order_id" in query.text


@pytest.mark.parametrize(("metric", "dimension", "start", "end"), [
    ("net", "category", "2026-09-01", "2026-10-01"),
    ("stock_qty", "none", "2026-10-30", "2026-10-31"),
    ("paid", "day", "2026-09-01", "2026-09-01"),
])
def test_unsupported_plans_are_rejected(catalog, metric, dimension, start, end):
    with pytest.raises(ValueError):
        compile_sql(Plan(metric=metric, dimension=dimension, start=start,
                         end=end, end_inclusive=False), catalog)
