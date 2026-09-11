"""Actual OS limits are applied exclusively inside a hidden test child."""

import json
import sys

import pytest

from reachy_brain.knowledge.process import capture_parser


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PARSER-NATIVE-MEMORY-CEILING")
def test_child_allocation_is_denied_by_os_limit(record_property):
    program = """
import json
from reachy_brain.knowledge.limits import limit_parser_memory
limit = limit_parser_memory(64 * 1024 * 1024)
small = bytearray(1024 * 1024)
try:
    excessive = bytearray(128 * 1024 * 1024)
except MemoryError:
    print(json.dumps({'limit': limit, 'allocation_denied': True, 'small_bytes': len(small)}))
else:
    raise RuntimeError('memory ceiling did not deny allocation')
"""
    status, output = capture_parser([sys.executable, "-c", program], timeout=10)
    assert status == "ok", "native memory-limit installation/allocation check failed"
    evidence = json.loads(output)
    assert evidence["allocation_denied"] and evidence["small_bytes"] == 1024 * 1024
    assert evidence["limit"]["bytes"] <= 64 * 1024 * 1024
    record_property("measurements", evidence)


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PARSER-LIMIT-FAILURE-NO-EXTRACTION")
def test_unavailable_limits_prevent_document_library_import():
    program = """
import sys
from reachy_brain.knowledge import worker
def unavailable():
    raise OSError('synthetic unavailable limit')
worker.limit_parser_memory = unavailable
worker.main()
assert 'reachy_brain.knowledge.parser' not in sys.modules
assert 'docx' not in sys.modules and 'pypdf' not in sys.modules
"""
    status, output = capture_parser([sys.executable, "-c", program], timeout=10)
    assert status == "ok"
    assert json.loads(output) == {"status": "resource_limits_unavailable", "passages": []}
