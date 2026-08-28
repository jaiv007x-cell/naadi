"""
Minimal-but-valid rubric and golden fixture pair for Ramesh Kale STEMI smoke.

Clinically incomplete by design — structurally exercises the authoring workflow:
blueprint attach, fixture pair, dry-run hash, submit gate, revert.
"""
from __future__ import annotations

from copy import deepcopy

from shared.schemas.trace import (
    DrugAdminEvent,
    EscalationEvent,
    OrderEvent,
    PhysioTrace,
)

from services.pratibimb.app.eval.rubric import Axis, Severity
from services.pratibimb.authoring.constants import (
    RAMESH_CASE_ID,
    RAMESH_DRAFT_ID,
    RAMESH_TENANT_ID,
)
from services.pratibimb.authoring.constants import FixtureKind
from services.pratibimb.authoring.store import CaseDraftStore

RAMESH_CASE_VERSION = "1.0.0"
RAMESH_RUBRIC_VERSION = "0.1.0-smoke"

# Smoke author — distinct from system.seed; used in walkthrough and e2e tests.
RAMESH_SMOKE_AUTHOR = "ramesh-author"

RAMESH_SMOKE_HITS: tuple[dict, ...] = (
    {
        "id": "stemi.ecg_ordered",
        "axis": Axis.ACTION.value,
        "matcher": "order_placed",
        "params": {"order_id": "ecg_12l", "within_s": 600},
        "points": 10.0,
        "severity": Severity.MAJOR.value,
        "required": True,
        "fail_case_on_violation": True,
        "description": "12-lead ECG ordered within 10 minutes",
    },
    {
        "id": "stemi.aspirin_chewed",
        "axis": Axis.ACTION.value,
        "matcher": "drug_given",
        "params": {
            "drug_id": "aspirin",
            "route": "PO",
            "min_dose": 300,
            "max_dose": 350,
        },
        "points": 10.0,
        "severity": Severity.MAJOR.value,
        "required": True,
        "fail_case_on_violation": True,
        "description": "Aspirin 325 mg chewed when not contraindicated",
    },
    {
        "id": "stemi.pci_transfer",
        "axis": Axis.ACTION.value,
        "matcher": "escalation",
        "params": {"target": "pci_transfer", "within_s": 900},
        "points": 5.0,
        "severity": Severity.MINOR.value,
        "required": False,
        "fail_case_on_violation": False,
        "description": "PCI-capable transfer arranged",
    },
)


def ramesh_smoke_grading_blueprint(*, case_id: str = RAMESH_CASE_ID) -> dict:
    return {
        "case_id": case_id,
        "case_version": RAMESH_CASE_VERSION,
        "rubric_version": RAMESH_RUBRIC_VERSION,
        "display_name": "Ramesh Kale — inferior STEMI (smoke rubric)",
        "pass_threshold": 0.70,
        "hits": [deepcopy(h) for h in RAMESH_SMOKE_HITS],
    }


def attach_smoke_rubric(blueprint_json: dict) -> dict:
    """Return blueprint JSON with the smoke grading_blueprint merged in."""
    merged = deepcopy(blueprint_json)
    merged["grading_blueprint"] = ramesh_smoke_grading_blueprint(
        case_id=str((merged.get("identity") or {}).get("case_id") or RAMESH_CASE_ID)
    )
    return merged


def ramesh_golden_pass_trace(
    *,
    case_id: str = RAMESH_CASE_ID,
    case_version: str = RAMESH_CASE_VERSION,
) -> dict:
    trace = PhysioTrace(case_id=case_id, case_version=case_version)
    trace.record_order(OrderEvent(order_id="ecg_12l", t_s=120.0))
    trace.record_drug(
        DrugAdminEvent(drug_id="aspirin", dose=325, dose_unit="mg", route="PO", t_s=180.0)
    )
    trace.record_escalation(EscalationEvent(target="pci_transfer", t_s=400.0))
    return trace.to_canonical()


def ramesh_critical_miss_trace(
    *,
    case_id: str = RAMESH_CASE_ID,
    case_version: str = RAMESH_CASE_VERSION,
) -> dict:
    """Passes aspirin but omits required ECG — must fail dry-run / grade."""
    trace = PhysioTrace(case_id=case_id, case_version=case_version)
    trace.record_drug(
        DrugAdminEvent(drug_id="aspirin", dose=325, dose_unit="mg", route="PO", t_s=180.0)
    )
    return trace.to_canonical()


def wire_ramesh_smoke(store: CaseDraftStore, draft_id: str = RAMESH_DRAFT_ID) -> bool:
    """
    Attach smoke rubric and golden fixture pair to the Ramesh draft.

    Returns True when wiring changed the draft, False when already wired.
    """
    draft = store.get_draft(draft_id)
    if draft is None:
        return False

    changed = False
    if draft.blueprint_json.get("grading_blueprint") is None:
        draft.blueprint_json = attach_smoke_rubric(draft.blueprint_json)
        draft.blueprint_version = RAMESH_CASE_VERSION
        changed = True

    fixtures = {row.fixture_kind for row in store.list_fixtures(draft_id)}
    if FixtureKind.PERFECT_PATH.value not in fixtures:
        store.upsert_fixture(
            draft_id,
            fixture_kind=FixtureKind.PERFECT_PATH,
            trace_json=ramesh_golden_pass_trace(),
            expected_grade_json={"passed": True},
        )
        changed = True
    if FixtureKind.CRITICAL_MISS.value not in fixtures:
        store.upsert_fixture(
            draft_id,
            fixture_kind=FixtureKind.CRITICAL_MISS,
            trace_json=ramesh_critical_miss_trace(),
            expected_grade_json={"passed": False},
        )
        changed = True

    return changed


def ensure_ramesh_smoke_wired(session) -> bool:
    """Startup helper — idempotently wire Ramesh when the draft exists."""
    store = CaseDraftStore(session)
    if store.get_draft(RAMESH_DRAFT_ID) is None:
        return False
    changed = wire_ramesh_smoke(store)
    if changed:
        session.commit()
    return changed
