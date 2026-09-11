"""Source-linked acceptance inventory; scenario success does not assess a whole gate."""

import hashlib
import re
from pathlib import Path


def acceptance_inventory(root=Path(".")):
    sources = {}
    requirements = []
    for relative in ("docs/iago/SPEC.md", "docs/iago/ACCEPTANCE.md"):
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError:
            sources[relative] = {"status": "unavailable"}
            continue
        sources[relative] = {"status": "available", "sha256": hashlib.sha256(raw).hexdigest()}
        lines = raw.decode("utf-8").splitlines()
        active = False
        section = ""
        for index, line in enumerate(lines):
            if relative.endswith("SPEC.md"):
                if line.startswith("## "):
                    active = line.startswith("## 18.")
                if not active:
                    continue
                if line.startswith("### "):
                    section = line[4:]
                if not line.startswith("| ") or line.startswith(("| Test |", "| ---")):
                    continue
                title, text = [part.strip() for part in line.strip("|").split("|", 1)]
                identifier = "SPEC18-" + re.sub(r"[^A-Z0-9]+", "-", title.upper()).strip("-")
            else:
                if line.startswith("## ") or line.startswith("### "):
                    section = line.lstrip("# ")
                original = re.match(r"([1-8])\. \*\*(.+?)\.\*\* (.+)", line)
                named = re.match(
                    r"- (?:\*\*)?((?:P10|V9|K1|E1)-[A-Z]+)(?::\*\*|\*\*:|:) (.+)", line
                )
                if original:
                    identifier, title, text = "GATE-" + original[1], original[2], original[3]
                elif named:
                    identifier, title, text = named[1], named[1], named[2]
                elif line.startswith("Create a versioned, non-secret evaluation corpus"):
                    identifier, title, text = "K1-CORPUS", "Document evaluation corpus", line
                elif line.startswith("Include the E1 sample workflow in the existing"):
                    identifier, title, text = (
                        "E1-MIXED-WORKLOAD",
                        "Integration mixed workload",
                        line,
                    )
                elif line == "### Mixed workload, timing and resumption":
                    identifier, title = "MIXED-WORKLOAD", "Mixed workload, timing and resumption"
                    end = next(
                        (n for n in range(index + 1, len(lines)) if lines[n].startswith("## ")),
                        len(lines),
                    )
                    text = "\n".join(lines[index + 1 : end]).strip()
                else:
                    continue
            requirements.append(
                {
                    "id": identifier,
                    "title": title,
                    "requirement": text,
                    "source": relative,
                    "line": index + 1,
                    "section": section,
                    "coverage_status": "not_assessed",
                    "qualifying_evidence": [],
                }
            )
    ids = [r["id"] for r in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_acceptance_requirement")
    return {
        "sources": sources,
        "status": "not_assessed"
        if all(s["status"] == "available" for s in sources.values())
        else "unavailable",
        "requirements": requirements,
        "notice": "Source inventory, not a coverage assessment. Full source documents remain authoritative, including supporting prose and evidence-tier rules. Scenario passes do not establish gate completion.",
    }
