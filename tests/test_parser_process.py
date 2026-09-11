"""Real hidden native subprocesses emit synthetic bytes, never parse personal files."""

import json
import sys

import pytest

from reachy_brain.knowledge.process import capture_parser


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PARSER-STREAM-OUTPUT-LIMIT")
@pytest.mark.parametrize(
    "mode,expected",
    [("exact", "ok"), ("overflow", "over_limit"), ("timeout", "timeout"), ("exit", "unreadable")],
)
def test_child_output_limit_timeout_and_exit(mode, expected):
    programs = {
        "exact": "import sys;sys.stdout.buffer.write(b'x'*1000)",
        "overflow": "import sys\nwhile True: sys.stdout.buffer.write(b'x'*65536);sys.stdout.buffer.flush()",
        "timeout": "import time;time.sleep(60)",
        "exit": "import sys;sys.stdout.write('partial');sys.exit(2)",
    }
    status, data = capture_parser(
        [sys.executable, "-c", programs[mode]], timeout=0.3 if mode == "timeout" else 5, limit=1000
    )
    assert status == expected
    assert data == (b"x" * 1000 if expected == "ok" else b"")


@pytest.mark.features("K1", "D5", "E1")
@pytest.mark.scenario("PARSER-PRIVATE-ENVIRONMENT-AND-STDERR")
def test_child_has_no_provider_credentials_and_diagnostics_are_discarded(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "synthetic-private-voice-key")
    monkeypatch.setenv("IAGO_ACCOUNT_TOKEN", "synthetic-account-token")
    program = "import os,sys,json;sys.stderr.write('synthetic diagnostic '*100000);print(json.dumps(sorted(os.environ)))"
    status, data = capture_parser([sys.executable, "-c", program], timeout=5)
    assert status == "ok"
    names = json.loads(data)
    assert not {"OPENAI_API_KEY", "ELEVENLABS_API_KEY", "IAGO_ACCOUNT_TOKEN"}.intersection(names)
    assert b"synthetic diagnostic" not in data


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PARSER-SINGLE-EXCESS-BYTE")
def test_excess_byte_is_detected_without_waiting_for_eof():
    program = "import sys,time;sys.stdout.buffer.write(b'x'*1001);sys.stdout.buffer.flush();time.sleep(60)"
    status, data = capture_parser([sys.executable, "-c", program], timeout=2, limit=1000)
    assert status == "over_limit" and data == b""
