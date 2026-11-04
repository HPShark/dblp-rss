"""Optional browser smoke test against the already indexed local cache.

Run with: venv/Scripts/python.exe tests/browser_search.py
Requires playwright and its Chromium headless shell; no Docker or remote download.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_dblp import LocalDBLP  # noqa: E402


def main():
    service = LocalDBLP()
    service.store.initialize()
    assert service.status()["catalog_ready"], "Build the local venue catalog before this browser check"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    output = ROOT / "test"
    output.mkdir(exist_ok=True)
    with (output / "search-browser-server.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            env=dict(os.environ, DBLP_CACHE_DIR=str(service.settings.cache_dir.resolve())),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert process.poll() is None, "uvicorn exited"
                try:
                    if requests.get(origin + "/health", timeout=1).status_code == 200:
                        break
                except requests.ConnectionError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("uvicorn did not start")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900},
                                              permissions=["clipboard-read", "clipboard-write"])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/search?q=TDSC")
                expect(page.locator(".card").first).to_contain_text("journals/tdsc")
                query = page.locator("#query")
                query.fill("IEEE Transactions on Dependable and Secure Computing")
                expect(page.locator(".card").first).to_contain_text("journals/tdsc")
                query.fill("CVPR")
                expect(page.locator(".card").first).to_contain_text("conf/cvpr")
                first = page.locator(".card").first
                link = first.locator(".link-row input").input_value()
                assert link == origin + "/dblp/stream%3Astreams%2Fconf%2Fcvpr%3A"
                first.get_by_role("button", name="复制链接").click()
                expect(page.locator("#notice")).to_have_text("订阅链接已复制。")
                assert page.evaluate("navigator.clipboard.readText()") == link
                page.screenshot(path=str(output / "search-desktop.png"), full_page=True)

                query.fill("")
                page.locator("#kind").select_option("conf")
                expect(page.locator(".card").first.locator(".badge")).to_have_text("会议")
                expect(page.locator("#pagination")).to_be_visible()
                page.locator("#next").click()
                expect(page.locator("#page")).to_contain_text("2 /")
                page.locator("#previous").click()
                expect(page.locator("#page")).to_contain_text("1 /")

                query.fill("unlikely_venue_that_does_not_exist_938572")
                expect(page.locator("#status")).to_contain_text("没有找到")
                expect(page.locator(".card")).to_have_count(0)
                expect(page.locator("#pagination")).to_be_hidden()

                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#kind").select_option("all")
                query.fill("TDSC")
                expect(page.locator(".card").first).to_contain_text("journals/tdsc")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(output / "search-mobile.png"), full_page=True)
                assert not errors, errors

                # Simulate a public HTTP domain at :8080 without DNS changes or
                # occupying that port. Proxy browser requests to the local server.
                public_origin = "http://papers.example.test:8080"

                def proxy_public_request(route):
                    response = requests.get(origin + route.request.url[len(public_origin):], timeout=10)
                    route.fulfill(status=response.status_code,
                                  headers={"Content-Type": response.headers["Content-Type"]},
                                  body=response.content)

                context.route(public_origin + "/**", proxy_public_request)
                public_page = context.new_page()
                public_page.on("pageerror", lambda error: errors.append(str(error)))
                public_page.goto(public_origin + "/search?q=TDSC")
                public_card = public_page.locator(".card").first
                expect(public_card).to_contain_text("journals/tdsc")
                expected_url = public_origin + "/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A"
                expect(public_card.locator(".link-row input")).to_have_value(expected_url)
                assert public_card.get_by_role("link").evaluate("a => a.href") == expected_url
                public_card.get_by_role("button").click()
                expect(public_page.locator("#notice")).to_have_text("订阅链接已复制。")
                assert not errors, errors
                browser.close()
            print("Browser checks passed: names, abbreviations, copy, origin, filters, pagination, empty state, mobile fit, HTTP domain with :8080")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
