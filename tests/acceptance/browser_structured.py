"""用不可达 Ollama 的服务验证表单、独立 SQL、确认边界和 CSV。"""
import csv
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from fixed_plan import PLANS, expected_rows


def create_from_form(page):
    with page.expect_response(
        lambda response: response.url.endswith("/sessions") and response.request.method == "POST"
    ) as created:
        page.locator("#plan-form button[type=submit]").click()
    response = created.value
    assert response.ok, response.status
    return response.json(), response.request.post_data_json


def trace_events(page, session_url):
    response = page.request.get(session_url + "/trace")
    assert response.ok, response.status
    events = response.json()["events"]
    assert events[0]["summary"]["source"] == "plan"
    assert not any(event["category"] == "model" for event in events)
    return [event["name"] for event in events]


def main():
    base_url = os.getenv("T2S_STRUCTURED_BROWSER_URL", "http://127.0.0.1:18331")
    artifacts = Path(".run/browser-structured")
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("T2S_BROWSER_CHANNEL"))
        page = browser.new_page(base_url=base_url, accept_downloads=True, viewport={"width": 1280, "height": 850})
        page.goto("/")
        expect(page.locator("#plan-form")).to_be_visible()
        for plan, rows in zip(PLANS, expected_rows(), strict=True):
            metric = plan["metric"]
            page.locator(f'[data-example="{metric}"]').click()
            pending, payload = create_from_form(page)
            assert payload == {"plan": {**plan, "end_inclusive": True}}, payload
            assert pending["stage"] == "confirm" and pending["executions"] == 0
            expect(page.locator("#plan-card")).to_contain_text(plan["end"])
            session_url = f"/sessions/{pending['id']}"
            assert "sql_started" not in trace_events(page, session_url)

            with page.expect_response(lambda response: response.url.endswith("/respond")) as confirmed:
                page.locator("#confirm").click()
            done = confirmed.value.json()
            assert done["stage"] == "done" and done["executions"] == 1
            assert done["rows"] == rows
            expect(page.locator("#results tbody tr")).to_have_count(len(rows))
            with page.expect_download() as download:
                page.locator("#download").click()
            output = artifacts / f"{metric}.csv"
            download.value.save_as(output)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                assert list(csv.reader(stream))[1:] == [[str(value) for value in row] for row in rows]

            repeat = page.request.post(
                session_url + "/respond", data={"action": "confirm", "revision": pending["revision"]}
            )
            assert repeat.status == 409
            names = trace_events(page, session_url)
            assert names.count("sql_started") == names.count("sql_finished") == 1
            assert names.index("approval_accepted") < names.index("sql_started")
            print(f"{metric}: {len(rows)} rows match independent SQL and CSV")

        pending, _ = create_from_form(page)
        session_url = f"/sessions/{pending['id']}"
        with page.expect_response(lambda response: response.url.endswith("/respond")) as cancelled:
            page.locator("#cancel").click()
        state = cancelled.value.json()
        assert state["stage"] == "cancelled" and state["executions"] == 0
        names = trace_events(page, session_url)
        assert "session_cancelled" in names and "sql_started" not in names

        expect(page.locator("#question-form")).to_be_visible()
        page.locator("#question").fill("2026年9月每天实付金额是多少？")
        with page.expect_response(lambda response: response.url.endswith("/sessions")) as asked:
            page.locator("#ask").click()
        assert set(asked.value.request.post_data_json) == {"question"}
        failing = asked.value.json()
        assert failing["stage"] == "error" and failing["executions"] == 0
        events = page.request.get(f"/sessions/{failing['id']}/trace").json()["events"]
        assert events[0]["summary"]["source"] == "question"
        assert any(event["name"] == "planner_failed" for event in events)

        page.locator("#new-session").click()
        page.screenshot(path=str(artifacts / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator("html").evaluate("(element) => element.scrollWidth === element.clientWidth")
        page.screenshot(path=str(artifacts / "mobile.png"))
        print("Offline model confirmed; cancel does not query and duplicate confirmation is blocked")
        browser.close()


if __name__ == "__main__":
    main()
