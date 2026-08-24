"""Seed the Ramesh Kale STEMI case into the authoring store at DRAFT."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from scripts.migrate_cases_v1_to_v2 import migrate_case_v1_to_v2
from services.pratibimb.app.case_gen.sampler import CORPUS_PATH
from services.pratibimb.authoring.constants import (
    RAMESH_CASE_ID,
    RAMESH_DRAFT_ID,
    RAMESH_TENANT_ID,
)
from services.pratibimb.authoring.ramesh_smoke import (
    RAMESH_SMOKE_AUTHOR,
    ensure_ramesh_smoke_wired,
    wire_ramesh_smoke,
)
from services.pratibimb.authoring.store import CaseDraftStore

log = logging.getLogger(__name__)

RAMESH_AUTHOR_SUBJECT = RAMESH_SMOKE_AUTHOR


def _load_ramesh_v1() -> dict:
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus: list[dict] = json.load(handle)
    for case in corpus:
        if case.get("probe_only"):
            continue
        payload = dict(case)
        payload["case_id"] = RAMESH_CASE_ID
        return payload
    raise RuntimeError(f"no non-probe case found in {CORPUS_PATH}")


def seed_ramesh_draft(
    session: Session,
    *,
    corpus_path: Path | None = None,
    wire_smoke: bool = True,
) -> str | None:
    """
    Idempotently insert Ramesh as the first authoring draft.

    Returns the draft id when created, or None when it already exists.
    """
    store = CaseDraftStore(session)
    existing = store.get_draft(RAMESH_DRAFT_ID)
    if existing is not None:
        if wire_smoke:
            if wire_ramesh_smoke(store):
                session.commit()
        return None

    if corpus_path is not None:
        with corpus_path.open(encoding="utf-8") as handle:
            corpus: list[dict] = json.load(handle)
        v1 = next(c for c in corpus if not c.get("probe_only"))
        v1 = dict(v1)
        v1["case_id"] = RAMESH_CASE_ID
    else:
        v1 = _load_ramesh_v1()

    result = migrate_case_v1_to_v2(v1)
    if not result.converted or result.blueprint is None:
        raise RuntimeError(
            f"failed to migrate Ramesh case: {result.missing_required}"
        )

    blueprint_json = result.blueprint.to_dict()
    store.create_draft(
        tenant_id=RAMESH_TENANT_ID,
        author_subject_id=RAMESH_AUTHOR_SUBJECT,
        blueprint_json=blueprint_json,
        blueprint_version=result.blueprint.version,
        draft_id=RAMESH_DRAFT_ID,
    )
    if wire_smoke:
        wire_ramesh_smoke(store)
    session.commit()
    log.info(
        "authoring.seed_ramesh draft_id=%s case_id=%s tier=%s defaulted=%d smoke_author=%s",
        RAMESH_DRAFT_ID,
        RAMESH_CASE_ID,
        result.tier.value,
        len(result.defaulted_needs_review),
        RAMESH_SMOKE_AUTHOR,
    )
    return RAMESH_DRAFT_ID


__all__ = [
    "RAMESH_AUTHOR_SUBJECT",
    "ensure_ramesh_smoke_wired",
    "seed_ramesh_draft",
]