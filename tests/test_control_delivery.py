"""Production browser delivery helper with controlled socket outcomes, no server receipt claim."""

import base64
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("BROWSER-CONTROL-DELIVERY-COUNTS")
def test_sent_unavailable_and_failed_are_distinct_without_payload_retention():
    url = (
        "data:text/javascript;base64,"
        + base64.b64encode(
            Path("reachy_brain/web/static/control_delivery.js").read_bytes()
        ).decode()
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.route(
                "https://iago.test/**",
                lambda route: route.fulfill(body="<html></html>", content_type="text/html"),
            )
            page.goto("https://iago.test/")
            result = page.evaluate(
                """async url => {
              const {sendControl,controlDeliverySnapshot} = await import(url);
              let sent=[];
              sendControl(null,{type:'speech_start',captured:123});
              sendControl({readyState:3},{type:'speech_start',captured:123});
              sendControl({readyState:1,send:s=>sent.push(s)},{type:'speech_start',captured:456});
              let thrown=false;
              try {sendControl({readyState:1,send:()=>{throw Error('fixture failure')}},{type:'stop',generation:3});}
              catch {thrown=true;}
              sendControl({readyState:1,send:s=>sent.push(s)},{type:'typed',text:'private content'});
              const first=controlDeliverySnapshot();
              first.counts.speech_start.sent=999;
              return {snapshot:controlDeliverySnapshot(),thrown,sent:sent.length};
            }""",
                url,
            )
            assert result["thrown"] and result["sent"] == 2
            assert result["snapshot"]["counts"] == {
                "speech_start": {"attempts": 3, "sent": 1, "unavailable": 2, "failed": 0},
                "stop": {"attempts": 1, "sent": 0, "unavailable": 0, "failed": 1},
            }
            assert "private content" not in str(result)
        finally:
            browser.close()
