"""F.c.a: NCVET observability belt — I-E3-10 grep guard for metric call site."""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_i_e3_10_single_call_site_for_record_ncvet_audit_metrics():
    """I-E3-10 option b: grep guard — exactly one increment call site in prod code."""
    call_sites: list[str] = []
    pratibimb = _REPO_ROOT / "services" / "pratibimb"
    for path in pratibimb.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("def record_ncvet_audit_metrics"):
                continue
            if "record_ncvet_audit_metrics(" not in line:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith(("import ", "from ")):
                continue
            call_sites.append(f"{rel}:{line_no}")
    assert call_sites == ["services/pratibimb/ledger_read/ncvet_audit.py:128"]
