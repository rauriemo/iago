"""Synthetic local documents changing between admission and decoder access."""

from pathlib import Path

import pytest
from docx import Document

from reachy_brain.knowledge import parser


@pytest.mark.features("K1")
@pytest.mark.scenario("PARSER-SOURCE-GROWTH-BOUND")
@pytest.mark.parametrize("suffix", [".txt", ".pdf", ".docx"])
def test_growth_after_stat_rejects_before_format_decoding(tmp_path, monkeypatch, suffix):
    target = tmp_path / ("growing" + suffix)
    target.write_bytes(b"small")
    original_stat = Path.stat
    grown = b"\xff" * (20 * 1024 * 1024 + 1)

    def growing(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            target.write_bytes(grown)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", growing)
        assert parser.extract(target) == {"status": "over_limit", "passages": []}
    assert target.read_bytes() == grown


@pytest.mark.features("K1")
@pytest.mark.scenario("PARSER-DOCX-VALIDATED-SNAPSHOT")
def test_docx_decoder_consumes_the_archive_that_was_checked(tmp_path, monkeypatch):
    target, replacement = tmp_path / "original.docx", tmp_path / "replacement.docx"
    for path, text in [
        (target, "Original copper optics."),
        (replacement, "Replacement silver optics."),
    ]:
        document = Document()
        document.add_paragraph(text)
        document.save(path)
    replacement_bytes = replacement.read_bytes()
    decode = parser.Document

    def replace_before_decode(source):
        target.write_bytes(replacement_bytes)
        return decode(source)

    monkeypatch.setattr(parser, "Document", replace_before_decode)
    result = parser.extract(target)
    assert result["status"] == "indexed"
    text = " ".join(p["text"] for p in result["passages"])
    assert "Original copper" in text and "Replacement silver" not in text
    assert target.read_bytes() == replacement_bytes
