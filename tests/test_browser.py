"""Real headless Chromium + loopback server, synthetic media; never a physical PC pass."""

import json
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import Connection, Rule, Tool
from reachy_brain.web.app import create_app


@pytest.mark.features("D1", "D4", "V1", "K1", "E1")
@pytest.mark.scenario("BROWSER-SYNTHETIC-CONTROLS")
def test_browser_camera_projects_and_tools(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "originals"
    root.mkdir()
    (root / "brief.txt").write_text("Synthetic observatory uses cobalt lenses.")
    installation = tmp_path / "installation.json"
    installation.write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "module": "counter",
                        "account": "synthetic-a",
                        "enabled": False,
                        "transport": "python",
                        "trusted": True,
                        "factory": "reachy_brain.integrations.fake_direct:create",
                    }
                ],
                "skills": [
                    {"path": "examples/workflows/fake-calendar", "trusted": True, "enabled": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            desktop_port=port,
            perception_enabled=True,
            integration_config=installation,
        ),
        token="synthetic-browser-token",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
            )
            try:
                page = browser.new_page(permissions=["camera", "microphone"])
                page.add_init_script("""window.iagoTestSockets=[];
                    window.WebSocket=class extends WebSocket {
                        constructor(...args){super(...args);
                            if(String(args[0]).endsWith('/control'))window.iagoTestSockets.push(this);
                        }
                    };""")
                page.add_init_script(
                    """window.mediaRequests=0;const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=(...args)=>{window.mediaRequests++;return capture(...args);};"""
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#token=synthetic-browser-token")
                expect(page.locator("#notice")).to_contain_text("Ready")
                assert page.evaluate("window.mediaRequests") == 0
                page.locator("#patient").check()
                page.locator("#mute").check()
                page.locator("#volume").evaluate(
                    "element=>{element.value='0.35';element.dispatchEvent(new Event('input',{bubbles:true}));}"
                )
                page.get_by_text("Speech voice", exact=True).click()
                # Synthetic protocol events exercise the real renderer, not a physical voice.
                page.evaluate("""() => {
                    for (const message of [
                        {type:'thinking'}, {type:'answer_partial',text:'Synthetic partial.'},
                        {type:'speech_error',message:'Speech unavailable. The answer will continue as text.'},
                        {type:'answer',text:'Synthetic complete answer.'}
                    ]) window.iagoTestSockets[0].onmessage({data:JSON.stringify(message)});
                }""")
                expect(page.locator("#notice")).to_have_text(
                    "Speech unavailable. Answer shown as text."
                )
                expect(page.get_by_text("Synthetic complete answer.", exact=True)).to_be_visible()
                page.locator("#voicePreview").click()
                expect(page.locator("#notice")).to_contain_text(
                    "Start Conversation before previewing"
                )
                assert page.evaluate("window.mediaRequests") == 0
                page.locator("#voiceProvider").select_option("openai")
                expect(page.locator("#voiceStatus")).to_have_text(
                    "Saved. Applies at the next conversation start."
                )
                page.get_by_text("Personality & language", exact=True).click()
                page.locator("#personalityText").fill(
                    "Speak concise English and respectfully challenge assumptions."
                )
                page.get_by_role("button", name="Save personality", exact=True).click()
                expect(page.locator("#personalityStatus")).to_have_text("Saved for future replies.")
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                page.locator("#camera").click()
                expect(page.locator("#previews video")).to_have_count(1)
                expect(page.locator("#history img").first).to_be_visible(timeout=10000)
                expect(page.locator("#perception")).to_contain_text("analyzed fps", timeout=15000)
                page.get_by_text("Devices & session details", exact=True).click()
                saved_devices = {}
                for control_id in ["microphone", "cameraDevice", "speaker"]:
                    control = page.locator(f"#{control_id}")
                    choice = control.evaluate(
                        "element=>[...element.options].find(option=>option.value)?.value||''"
                    )
                    control.select_option(choice)
                    saved_devices[control_id] = choice
                page.get_by_text("Project documents", exact=True).click()
                page.locator("#projectName").fill("Observatory")
                page.locator("#projectRoot").fill(str(root))
                page.get_by_role("button", name="Add folder", exact=True).click()
                page.get_by_role("button", name="Use", exact=True).click()
                expect(page.locator("#projects")).to_contain_text("indexed", timeout=15000)
                page.locator("#documentQuery").fill("cobalt")
                page.get_by_role("button", name="Search", exact=True).click()
                expect(page.locator("#documentResults")).to_contain_text("cobalt lenses")
                expect(page.locator("#documentResults")).to_contain_text("brief.txt")
                page.get_by_text("Integrations & workflows", exact=True).click()
                expect(page.locator("#integrations")).to_contain_text(
                    "documents__active__search_project_documents"
                )
                permission = page.get_by_label(
                    "Permission for documents__active__search_project_documents", exact=True
                )
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    permission.select_option("deny")
                assert saved.value.status == 200
                expect(permission).to_have_value("deny")
                workflow = page.get_by_label("Enable workflow fake-calendar-review", exact=True)
                module = page.get_by_label("Enable module counter / synthetic-a", exact=True)
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    module.check()
                assert saved.value.status == 200
                expect(page.locator("#integrations")).to_contain_text(
                    "Restart application to connect"
                )
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    workflow.uncheck()
                assert saved.value.status == 200
                page.reload()
                expect(page.locator("#patient")).to_be_checked()
                expect(page.locator("#mute")).to_be_checked()
                expect(page.locator("#volume")).to_have_value("0.35")
                for control_id, choice in saved_devices.items():
                    expect(page.locator(f"#{control_id}")).to_have_value(choice)
                assert page.evaluate("window.mediaRequests") == 0
                expect(page.locator("#voiceProvider")).to_have_value("openai")
                expect(page.locator("#personalityText")).to_have_value(
                    "Speak concise English and respectfully challenge assumptions."
                )
                page.get_by_text("Integrations & workflows", exact=True).click()
                expect(permission).to_have_value("deny")
                expect(workflow).not_to_be_checked()
                expect(module).to_be_checked()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/api/integrations")
                        and response.request.method == "POST"
                    )
                ) as saved:
                    permission.select_option("allow")
                assert saved.value.status == 200
                expect(permission).to_have_value("allow")
                journal = app.state.executor.operations
                journal.prepare("browser-cancel", "synthetic-digest", "fake__local__write", 0)
                page.locator("#refreshOperations").click()
                row = page.locator('[data-operation="browser-cancel"]')
                expect(row).to_contain_text("proposed")
                row.get_by_role("button", name="Cancel before dispatch", exact=True).click()
                expect(row).to_contain_text("canceled-before-dispatch")
                expect(row.get_by_role("button")).to_have_count(0)
                executor = app.state.executor
                connection = Connection("synthetic", "local")
                executor.registry.add_connection(connection)
                canceled = []

                async def fake_request(payload, context):
                    canceled.append(payload["operation_id"])
                    return {"outcome": "too_late", "provider_ref": context.operation_id}

                cancel_tool = Tool(
                    "synthetic",
                    "local",
                    "cancel",
                    "Synthetic cancellation",
                    {
                        "type": "object",
                        "properties": {"operation_id": {"type": "string"}},
                        "required": ["operation_id"],
                    },
                    {"type": "object"},
                    fake_request,
                    action="write",
                )
                original = Tool(
                    "synthetic",
                    "local",
                    "write",
                    "Synthetic seeded operation",
                    {"type": "object"},
                    {"type": "object"},
                    fake_request,
                    action="write",
                    cancel_tool=cancel_tool.key,
                )
                executor.registry.register(original)
                executor.registry.register(cancel_tool)
                executor.policy.set(Rule(cancel_tool.key, "write", "confirm"))
                journal.begin(
                    "browser-provider",
                    "synthetic-seed",
                    original.key,
                    0,
                    executor.account_ref(connection),
                )
                journal.finish("browser-provider", "uncertain")
                page.locator("#refreshOperations").click()
                provider_row = page.locator('[data-operation="browser-provider"]')
                dialogs = []

                def approve(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept()

                page.once("dialog", approve)
                provider_row.get_by_role(
                    "button", name="Request provider cancellation", exact=True
                ).click()
                expect(page.locator("#notice")).to_contain_text("too_late")
                assert canceled == ["browser-provider"]
                assert len(dialogs) == 1 and '"operation_id":"browser-provider"' in dialogs[0]
                assert "Account: local" in dialogs[0]
                assert journal.get("browser-provider")["status"] == "uncertain"
                page.locator("#exportResources").evaluate(
                    "button=>button.closest('details').open=true"
                )
                with page.expect_download() as downloaded:
                    page.locator("#exportResources").click()
                resource_file = tmp_path / "resource-export.json"
                downloaded.value.save_as(resource_file)
                resource_data = json.loads(resource_file.read_text(encoding="utf-8"))
                assert 0 < len(resource_data["samples"]) <= resource_data["capacity"]
                assert resource_data["latest"]["backend_rss_bytes"] > 0
                page.locator("#end").click()
                expect(page.locator("#state")).to_contain_text("idle")
                expect(page.locator("#previews video")).to_have_count(0)
                page.evaluate(
                    "localStorage.setItem('iago-device-preferences',JSON.stringify({microphone:'synthetic-missing-device',volume:99,patient:'invalid'}))"
                )
                page.reload()
                expect(page.locator("#microphone")).to_have_value("synthetic-missing-device")
                expect(page.locator("#microphone option:checked")).to_have_text(
                    "Saved device unavailable — choose another"
                )
                expect(page.locator("#volume")).to_have_value("0.8")
                expect(page.locator("#patient")).not_to_be_checked()
                page.locator("#aware").click()
                expect(page.locator("#state")).to_contain_text("aware")
                assert page.evaluate("window.mediaRequests") == 0
                page.locator("#end").click()
                expect(page.locator("#state")).to_contain_text("idle")
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(15)
        assert not worker.is_alive()
