"""H.c.2: fail-closed sink-failure meta-test harden (file:function anchors).

This module asserts the registered sites where audit emission fails *closed* on
sink failure — the calling request returns error rather than proceeding without
an audit trail.

H closed with five; I.a + I.b added two ARP sites → **seven** anchors;
I.c sets ARP ``surface=`arp``` (historical ``regrade`` samples unchanged).

**In registry:** labeled ``AUDIT_SINK_FAILURE_TOTAL.labels(... surface=...).inc()``
in production fail-closed handlers.

**Out of registry (do not "fix" by adding these):**
  (a) happy-path audit emits (issuer mint success, verify success) that are not
      gated on sink-failure → 503 semantics;
  (b) best-effort / fail-open emits (metrics, non-compliance observability);
  (c) test fixtures and migration scripts (non-production paths).

**Inverse invariant:** adding a new fail-closed sink-failure site is legitimate,
but requires the four-part landing before updating this registry:
  site + runbook entry + PromQL alert + threshold-review owner.
Otherwise the meta-test becomes a rubber stamp.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from services.pratibimb.regrade.verify_audit import FETCH_ARTIFACT_KIND, VERIFY_ASSIST_KIND

pytestmark = pytest.mark.h_acceptance

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRATIBIMB = _REPO_ROOT / "services" / "pratibimb"
_RUNBOOK = _REPO_ROOT / "docs" / "ops" / "credentials_regrade_observability.md"

# Literal closed set (pin 2) — must match runbook; do not infer from emitters.
_CLOSED_SURFACES = frozenset({"catalog", "ncvet", "credentials", "regrade", "arp"})

# Pin 1 / 4: exact fail-closed sink-failure anchors (file:function).
# I.c: ARP sites surface=arp (mint + verify).
_EXPECTED_FAIL_CLOSED_SITES: frozenset[str] = frozenset(
    {
        "ledger_read/catalog_audit.py:catalog_safe_emit",
        "ledger_read/ncvet_audit.py:ncvet_safe_emit",
        "credentials/status_service.py:fetch_status_list",
        "regrade/service.py:_write_audit",
        "regrade/verify_audit.py:emit_regrade_verify_audit",
        "arp/service.py:_write_audit",
        "arp/verify_audit.py:emit_arp_verify_audit",
    }
)

# Pin 3: mint has no ledger_read query_kind (dedicated regrade_audit table).
# Shape A uses these ledger query_kinds — sets must stay disjoint.
_MINT_QUERY_KINDS: frozenset[str] = frozenset()
_SHAPE_A_QUERY_KINDS: frozenset[str] = frozenset(
    {VERIFY_ASSIST_KIND, FETCH_ARTIFACT_KIND}
)


def _rel(path: Path) -> str:
    return path.relative_to(_PRATIBIMB).as_posix()


def _function_hosts_sink_failure_inc(source: str, node: ast.AST) -> bool:
    seg = ast.get_source_segment(source, node) or ""
    if "AUDIT_SINK_FAILURE_TOTAL" not in seg:
        return False
    if ".labels(" not in seg or ".inc()" not in seg:
        return False
    return bool(re.search(r"surface\s*=", seg))


def _surface_label_in_span(seg: str, module_source: str) -> str | None:
    m = re.search(r'surface\s*=\s*["\']([^"\']+)["\']', seg)
    if m:
        return m.group(1)
    if re.search(r"surface\s*=\s*_SURFACE", seg):
        cm = re.search(r'_SURFACE\s*=\s*["\']([^"\']+)["\']', module_source)
        if cm:
            return cm.group(1)
    return None


def _discover_labeled_fail_closed_sites() -> set[str]:
    found: set[str] = set()
    for path in _PRATIBIMB.rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _function_hosts_sink_failure_inc(source, node):
                    found.add(f"{_rel(path)}:{node.name}")
    return found


def _discover_registered_surfaces() -> set[str]:
    surfaces: set[str] = set()
    for path in _PRATIBIMB.rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(source, node) or ""
                if not _function_hosts_sink_failure_inc(source, node):
                    continue
                label = _surface_label_in_span(seg, source)
                if label is not None:
                    surfaces.add(label)
    return surfaces


def test_h_c_2_1_exact_file_function_anchors():
    """
    Fail-closed sink-failure sites only (not all audit emits).

    Per-anchor asserts so each missing site names itself in failure output.
    """
    found = _discover_labeled_fail_closed_sites()
    for anchor in sorted(_EXPECTED_FAIL_CLOSED_SITES):
        assert anchor in found, (
            f"{anchor} not found in registered fail-closed sites"
        )
        rel, func = anchor.split(":", 1)
        path = _PRATIBIMB.joinpath(*rel.split("/"))
        assert path.is_file(), f"{anchor}: file missing"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        spans = [
            ast.get_source_segment(source, n) or ""
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
        ]
        assert spans, f"{anchor} not found in registered fail-closed sites"
        assert any(
            "AUDIT_SINK_FAILURE_TOTAL" in s and ".inc()" in s for s in spans
        ), f"{anchor} does not host AUDIT_SINK_FAILURE_TOTAL.inc()"


def test_h_c_2_2_closed_surface_set_literal():
    assert _CLOSED_SURFACES == frozenset(
        {"catalog", "ncvet", "credentials", "regrade", "arp"}
    ), _CLOSED_SURFACES
    registered = _discover_registered_surfaces()
    unexpected = sorted(registered - _CLOSED_SURFACES)
    for surface in unexpected:
        raise AssertionError(
            f"surface `{surface}` present in registered emitters but not in "
            f"closed set {{{', '.join(sorted(_CLOSED_SURFACES))}}}"
        )
    missing = sorted(_CLOSED_SURFACES - registered)
    for surface in missing:
        raise AssertionError(
            f"surface `{surface}` in closed set "
            f"{{{', '.join(sorted(_CLOSED_SURFACES))}}} but has no registered "
            f"fail-closed emitter (erosion)"
        )
    text = _RUNBOOK.read_text(encoding="utf-8")
    for surface in _CLOSED_SURFACES:
        assert f"`{surface}`" in text or f'surface="{surface}"' in text, surface
    for forbidden in ("authoring", "ledger", "regrade_verifier"):
        assert f'surface="{forbidden}"' not in text


def test_h_c_2_3_mint_and_shape_a_query_kinds_disjoint():
    collided = sorted(_MINT_QUERY_KINDS & _SHAPE_A_QUERY_KINDS)
    for kind in collided:
        raise AssertionError(
            f"query_kind '{kind}' present in both _MINT_QUERY_KINDS and "
            f"_SHAPE_A_QUERY_KINDS; mint and read-audit namespaces must stay disjoint"
        )
    assert _SHAPE_A_QUERY_KINDS == frozenset(
        {"verify_regrade_assist", "fetch_regrade_artifact"}
    )
    # Belt: mint module must not contain the literal Shape A kind *values*
    # (members of _SHAPE_A_QUERY_KINDS), not a regex over kind patterns.
    mint_src = (_PRATIBIMB / "regrade" / "service.py").read_text(encoding="utf-8")
    for kind in _SHAPE_A_QUERY_KINDS:
        assert kind not in mint_src, (
            f"query_kind '{kind}' (Shape A / _SHAPE_A_QUERY_KINDS member) "
            f"found in mint module regrade/service.py; namespaces must stay disjoint"
        )
    assert "RegradeAuditRow" in mint_src
    verify_src = (_PRATIBIMB / "regrade" / "verify_audit.py").read_text(encoding="utf-8")
    for kind in _SHAPE_A_QUERY_KINDS:
        assert kind in verify_src, f"Shape A missing query_kind constant {kind}"
    assert "RegradeAuditRow" not in verify_src


def test_h_c_2_4_exactly_labeled_fail_closed_sites():
    """
    Exactly the registered fail-closed *sink-failure* sites (pin 4).

    Asserts sites where audit emission fails *closed* on sink failure — the
    calling request returns error rather than proceeding without an audit trail.

    Exclusions (do not add to registry to "fix" a green suite):
      (a) happy-path audit emits (issuer mint / verify success);
      (b) best-effort / fail-open emits;
      (c) test fixtures and migration scripts.

    Inverse: a new fail-closed sink-failure site requires four-part landing
    (site + runbook + PromQL alert + threshold-review owner) before extending
    ``_EXPECTED_FAIL_CLOSED_SITES``.
    """
    found = _discover_labeled_fail_closed_sites()
    for anchor in sorted(_EXPECTED_FAIL_CLOSED_SITES):
        assert anchor in found, (
            f"{anchor} not found in registered fail-closed sites"
        )
    for anchor in sorted(found - _EXPECTED_FAIL_CLOSED_SITES):
        raise AssertionError(
            f"unexpected fail-closed site '{anchor}' discovered but not in the "
            f"registered anchors; add runbook entry + PromQL alert + "
            f"threshold review before registering"
        )
    assert len(found) == len(_EXPECTED_FAIL_CLOSED_SITES), (
        f"expected exactly {len(_EXPECTED_FAIL_CLOSED_SITES)} fail-closed "
        f"sink-failure sites, got {len(found)}: {sorted(found)}"
    )


def test_h_c_2_5_runbook_lists_same_file_function_anchors():
    """Manual sync: registry anchors must appear in the runbook call-site table."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    for anchor in sorted(_EXPECTED_FAIL_CLOSED_SITES):
        file_part, func = anchor.split(":", 1)
        assert file_part in text, (
            f"runbook missing file path for {anchor}; keep "
            f"_EXPECTED_FAIL_CLOSED_SITES in sync with "
            f"credentials_regrade_observability.md"
        )
        assert func in text, (
            f"runbook missing function {func} for {anchor}; keep registry "
            f"and runbook call-site table in sync"
        )
