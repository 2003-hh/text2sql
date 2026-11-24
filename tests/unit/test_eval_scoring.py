from evals.scoring import compare_plan, trace_findings


EXPECTED = {
    "metric": "paid", "start": "2026-09-01", "end": "2026-10-01",
    "dimension": "day", "category": None,
}


def test_inclusive_and_exclusive_end_are_equivalent():
    actual = dict(EXPECTED, end="2026-09-30", end_inclusive=True)
    assert compare_plan(EXPECTED, actual) == {
        field: True for field in ("metric", "date", "dimension", "category")
    }


def test_shifted_date_and_missing_category_are_field_failures():
    actual = dict(EXPECTED, end_inclusive=True)
    del actual["category"]
    assert compare_plan(EXPECTED, actual) == {
        "metric": True, "date": False, "dimension": True, "category": False,
    }


def test_query_before_approval_or_in_another_request_is_a_violation():
    sql = {"name": "sql_started", "request_id": "request", "revision": 1}
    approval = {"name": "approval_accepted", "request_id": "approve", "revision": 1}
    assert trace_findings([sql, approval], approval_sent=True)
    assert trace_findings([approval, sql], approval_sent=True)
    assert trace_findings([sql], approval_sent=False)
    assert trace_findings([approval, dict(sql, request_id="approve")], approval_sent=True) == []


def test_double_execution_is_a_violation():
    event = {"request_id": "request", "revision": 1}
    approval = dict(event, name="approval_accepted")
    sql = dict(event, name="sql_started")
    assert trace_findings([approval, sql, sql], approval_sent=True)
