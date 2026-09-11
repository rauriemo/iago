"""Real Chromium/HTTP with the full synthetic candidate corpus; no provider or physical claims."""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.evals.k1_corpus import materialize
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D1", "D4")
@pytest.mark.scenario("K1-CANDIDATE-BROWSER-CONTROLS")
def test_browser_all_candidate_queries_reindex_remove_and_late_response(
    tmp_path, record_property, monkeypatch
):
    class ObservedConversation(Conversation):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.test_loop = asyncio.get_running_loop()

    monkeypatch.setattr("reachy_brain.web.app.Conversation", ObservedConversation)
    corpus = materialize(tmp_path / "corpus")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", desktop_port=port),
        token="test-browser",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#token=test-browser")
                expect(page.locator("#notice")).to_contain_text("Ready")
                page.get_by_text("Project documents", exact=True).click()
                expect(page.locator("#addProject").locator("..")).to_contain_text(
                    "persist separately"
                )
                for name, root in corpus["projects"].items():
                    page.locator("#projectName").fill(name)
                    page.locator("#projectRoot").fill(str(root))
                    page.get_by_role("button", name="Add folder", exact=True).click()
                    expect(page.locator(f'[data-project="{name}"]')).to_be_visible()
                for name in corpus["projects"]:
                    expect(page.locator(f'[data-project="{name}"]')).to_contain_text(
                        '"indexed":10', timeout=30000
                    )

                def use(name):
                    page.locator(f'[data-project="{name}"]').get_by_role(
                        "button", name="Use", exact=True
                    ).click()
                    expect(page.locator(f'[data-project="{name}"] > p').first).to_contain_text(
                        f"● {name}"
                    )

                def search(query):
                    page.locator("#documentQuery").fill(query)
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/projects")
                            and response.request.post_data_json.get("action") == "search"
                        )
                    ):
                        page.get_by_role("button", name="Search", exact=True).click()

                variants = 0
                for name in corpus["projects"]:
                    use(name)
                    for question in (
                        q for q in corpus["questions"]["answerable"] if q["project"] == name
                    ):
                        for query in question["variants"]:
                            search(query)
                            for support in question["supports"]:
                                expect(page.locator("#documentResults")).to_contain_text(
                                    support["fact"]
                                )
                                expect(page.locator("#documentResults")).to_contain_text(
                                    support["path"]
                                )
                                expect(page.locator("#documentResults")).to_contain_text(
                                    f"{support['locator_kind']} {support['locator']}"
                                )
                            variants += 1
                assert variants == 45
                use("aster")
                core = app.state.active["conversation"]
                rows = app.state.projects.search(app.state.projects.active, "launch site")
                # Exercise the real control transport/rendering with a synthetic
                # evidence event; this is not an Astra-answer qualification.
                asyncio.run_coroutine_threadsafe(
                    core.emit("evidence", frames=rows), core.test_loop
                ).result(3)
                expect(page.locator("#evidence blockquote").first).to_contain_text("Lisbon")
                expect(page.locator("#evidence")).to_contain_text("lines 1")
                page.locator('[data-project="aster"]').get_by_text(
                    "File coverage", exact=True
                ).click()
                expect(page.locator('[data-project="aster"]')).to_contain_text(
                    "scan-one.pdf: requires_ocr"
                )
                expect(page.locator('[data-project="aster"]')).to_contain_text(
                    "malformed.docx: unreadable"
                )
                page.locator('[data-project="aster"]').get_by_role(
                    "button", name="Reindex", exact=True
                ).click()
                expect(page.locator('[data-project="aster"]')).to_contain_text(
                    '"indexed":10', timeout=15000
                )

                delayed = []

                def intercept(route):
                    if route.request.post_data_json.get("action") == "search" and not delayed:
                        delayed.append((route, route.fetch()))
                    else:
                        route.continue_()

                page.route("**/api/projects", intercept)
                page.locator("#documentQuery").fill("launch site")
                page.get_by_role("button", name="Search", exact=True).click()
                deadline = time.monotonic() + 5
                while not delayed and time.monotonic() < deadline:
                    page.wait_for_timeout(10)
                assert delayed
                use("boreal")
                expect(page.locator("#evidence")).to_be_empty()
                search("launch site")
                expect(page.locator("#documentResults")).to_contain_text("Oslo")
                route, response = delayed[0]
                with page.expect_request_finished(lambda request: request == route.request):
                    route.fulfill(response=response)
                page.wait_for_timeout(100)
                expect(page.locator("#documentResults")).to_contain_text("Oslo")
                expect(page.locator("#documentResults")).not_to_contain_text("Lisbon")
                page.unroute("**/api/projects", intercept)
                page.locator('[data-project="boreal"]').get_by_role(
                    "button", name="Remove index", exact=True
                ).click()
                expect(page.locator('[data-project="boreal"]')).to_have_count(0)
                expect(page.locator("#documentResults")).to_be_empty()
                assert (corpus["projects"]["boreal"] / "brief.md").is_file()

                # Exercise recovery control with a declared synthetic HTTP error,
                # followed by actual backend SQLite compaction on retry.
                def cleanup_error(route):
                    route.fulfill(status=409, json={"error": "index_storage_cleanup_failed"})

                page.route("**/api/projects", cleanup_error)
                page.get_by_role("button", name="Retry storage cleanup", exact=True).click()
                expect(page.locator("#notice")).to_contain_text("Storage cleanup could not finish")
                page.unroute("**/api/projects", cleanup_error)
                page.get_by_role("button", name="Retry storage cleanup", exact=True).click()
                expect(page.locator("#notice")).to_have_text(
                    "Project index storage cleanup completed."
                )
                page.locator("#exportProjectTimings").evaluate(
                    "button=>button.closest('details').open=true"
                )
                with page.expect_download() as download:
                    page.get_by_role("button", name="Export project timings", exact=True).click()
                timing_file = tmp_path / "project-timings.json"
                download.value.save_as(timing_file)
                timings = json.loads(timing_file.read_text(encoding="utf-8"))
                assert timings["counts"]["search/ok"] >= 45
                assert 0 < len(timings["samples"]) <= timings["sample_limit"] == 256
                assert all(
                    set(row) == {"operation", "seconds", "outcome", "sequence"}
                    for row in timings["samples"]
                )
                assert not errors
                record_property("sample_count", variants)
                record_property(
                    "measurements",
                    {
                        "browser": browser.version,
                        "query_variants": variants,
                        "fixture": corpus["manifest"]["version"],
                        "physical": False,
                    },
                )
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(15)
        assert not worker.is_alive()
