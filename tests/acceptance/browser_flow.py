"""Use a real Edge browser, Ollama, Redis and MySQL for the V16 page flow."""

import csv
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    base_url = os.getenv("T2S_BROWSER_URL", "http://127.0.0.1:18330")
    artifacts = Path(".run/browser")
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("T2S_BROWSER_CHANNEL"), headless=True)
        page = browser.new_page(base_url=base_url, accept_downloads=True)
        page.goto("/")
        expect(page.locator("#choice-metric option")).to_have_count(6)
        page.locator("#question").fill("每天键盘实付金额是多少？")
        with page.expect_response(lambda r: r.url.endswith("/sessions") and r.request.method == "POST",
                                  timeout=125_000) as created:
            page.locator("#ask").click()
        pending = created.value.json()
        assert pending.get("stage") == "clarify", pending.get("reason")
        assert pending["executions"] == 0
        assert pending["decision"]["missing_fields"] == ["date"], pending["decision"]["missing_fields"]
        expect(page.locator("#clarify-form")).to_be_visible()
        trace = page.locator("#trace-panel")
        expect(trace).to_be_visible()
        assert trace.get_attribute("open") is None
        trace.locator(":scope > summary").focus()
        trace.locator(":scope > summary").press("Enter")
        expect(trace.get_by_text("待补充：日期")).to_be_visible()
        assert trace.locator('[data-event-name="sql_started"]').count() == 0
        page.locator("#choice-start").fill("2026-09-01")
        page.locator("#choice-end").fill("2026-09-30")
        with page.expect_response(lambda r: r.url.endswith("/respond")) as chosen:
            page.locator("#clarify-form button").click()
        proposed = chosen.value.json()
        assert proposed.get("stage") == "confirm", proposed.get("reason")
        assert proposed["revision"] == 1 and proposed["executions"] == 0
        assert proposed["decision"]["plan"]["category"] == "键盘"
        assert proposed["decision"]["plan"]["dimension"] == "day"
        expect(trace.locator('[data-event-name="approval_required"] .trace-event-name')).to_be_visible()
        expect(trace.locator("#trace-stage")).to_have_text("待确认")
        assert trace.locator('[data-event-name="sql_started"]').count() == 0

        page.reload()
        expect(page.locator("#confirm")).to_be_visible()
        assert page.request.post(f"/sessions/{pending['id']}/respond",
                                 data={"revision": 0, "action": "confirm"}).status == 409
        assert trace.get_attribute("open") is None
        trace.locator(":scope > summary").click()
        expect(trace.locator('[data-event-name="approval_required"] .trace-event-name')).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith("/respond")) as confirmed:
            page.locator("#confirm").click()
        done = confirmed.value.json()
        assert done["stage"] == "done" and done["executions"] == 1, done.get("reason")
        assert len(done["rows"]) > 0
        expect(page.locator("#results tbody tr")).to_have_count(len(done["rows"]))
        expect(trace.locator('[data-event-name="sql_started"]')).to_have_count(1)
        expect(trace.locator('[data-event-name="sql_finished"]')).to_have_count(1)
        expect(trace.get_by_text("返回 30 行")).to_be_visible()
        page.reload()
        expect(page.locator("#results tbody tr")).to_have_count(len(done["rows"]))
        with page.expect_download() as download:
            page.locator("#download").click()
        output = artifacts / "paid-keyboard.csv"
        download.value.save_as(output)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            exported = list(csv.reader(stream))
        assert exported[1:] == [[str(value) for value in row] for row in done["rows"]]
        assert page.request.get(f"/sessions/{pending['id']}").json()["executions"] == 1
        (artifacts / "result.json").write_text(
            json.dumps({"session": pending["id"], "rows": len(done["rows"]),
                        "revision": done["revision"], "executions": done["executions"],
                        "restored": True, "csv_matches": True}, indent=2),
            encoding="utf-8",
        )
        expect(trace.locator("#trace-stage")).to_have_text("已完成")
        trace.locator(":scope > summary").click()
        expect(trace.get_by_text("返回 30 行")).to_be_visible()
        assert trace.locator("#trace-raw").get_attribute("href") == f"/sessions/{pending['id']}/trace"
        events = page.request.get(trace.locator("#trace-raw").get_attribute("href")).json()["events"]
        first_event = next(event for event in events if event["name"] == "session_created")
        node = trace.locator('[data-event-name="session_created"]')
        toggle = node.locator("summary")
        details = node.locator(".trace-event-content")
        expect(details).to_be_hidden()
        toggle.click()
        expect(details).to_be_visible()
        fields = {key: value for key, value in first_event.items() if key != "summary"}
        expect(details.locator(":scope > dl [data-field]")).to_have_count(len(fields))
        for key, value in (fields | first_event["summary"]).items():
            field = details.locator(f'[data-field="{key}"]')
            expected = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            expect(field.locator(".trace-field-value")).to_have_text(expected)
            expect(field.locator(".trace-field-description")).not_to_be_empty()

        completed = trace.locator('[data-event-name="sql_finished"]')
        completed.locator("summary").press("Enter")
        expect(completed.locator('[data-field="row_count"] .trace-field-value')).to_have_text(
            str(len(done["rows"]))
        )
        completed.locator("summary").press("Enter")
        expect(completed.locator(".trace-event-content")).to_be_hidden()
        with page.expect_response(lambda response: "/trace?" in response.url):
            trace.locator("#trace-refresh").click()
        expect(trace.locator("#trace-events")).to_have_attribute("aria-busy", "false")
        expect(details).to_be_visible()
        node.screenshot(path=str(artifacts / "trace-node-desktop.png"))
        trace.screenshot(path=str(artifacts / "trace-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert trace.evaluate("(element) => element.scrollWidth === element.clientWidth")
        assert page.locator("html").evaluate("(element) => element.scrollWidth === element.clientWidth")
        assert details.evaluate("(element) => element.scrollWidth === element.clientWidth")
        node.screenshot(path=str(artifacts / "trace-node-mobile.png"))
        trace.screenshot(path=str(artifacts / "trace-mobile.png"))
        page.screenshot(path=str(artifacts / "result-page.png"), full_page=True)
        print(f"Browser workflow: clarify -> confirm -> done; {len(done['rows'])} rows match CSV")
        browser.close()


if __name__ == "__main__":
    main()
