"""
Phase E1: resolve + verify published blueprint hashes at ledger finalize.

Create-time snapshot is authoritative for the stamp; finalize verifies it
against ``published_case_versions`` (including rows with ``retired_at`` set —
mid-session retire must still grade). Not-found is distinct from drift.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services.pratibimb.authoring.errors import (
    BlueprintHashDriftError,
    PublishedCaseNotFoundError,
)
from services.pratibimb.authoring.store import CaseDraftStore

log = logging.getLogger(__name__)


def resolve_published_content_hash(
    authoring_session: Session,
    *,
    tenant_id: str,
    case_id: str,
    case_version: str,
) -> str:
    """
    Return ``published_case_versions.content_hash`` for the tenant triple.

    Raises ``PublishedCaseNotFoundError`` when absent. Retirement does not
    hide the row — finalize may stamp against a since-retired version.
    """
    store = CaseDraftStore(authoring_session)
    row = store.get_published_version(
        tenant_id=tenant_id,
        case_id=case_id,
        version=case_version,
    )
    if row is None:
        raise PublishedCaseNotFoundError(
            tenant_id=tenant_id,
            case_id=case_id,
            case_version=case_version,
        )
    return row.content_hash


def verify_blueprint_hash_at_finalize(
    authoring_session: Session,
    *,
    session_id: str,
    tenant_id: str,
    case_id: str,
    case_version: str,
    stamped_hash: str | None,
) -> None:
    """
    Fail-closed finalize gate.

    - ``stamped_hash is None`` → skip (legacy / pre-E1; null is the audit signal)
    - published missing → ``PUBLISHED_CASE_NOT_FOUND``
    - published hash ≠ stamped → ``BLUEPRINT_HASH_DRIFT`` (no append)
    - published retired but hash matches → ok
    """
    if stamped_hash is None:
        return

    published_hash = resolve_published_content_hash(
        authoring_session,
        tenant_id=tenant_id,
        case_id=case_id,
        case_version=case_version,
    )
    if published_hash == stamped_hash:
        return

    log.error(
        "ledger.blueprint_hash_drift session=%s case=%s@version=%s "
        "stamped=%s published=%s",
        session_id,
        case_id,
        case_version,
        stamped_hash,
        published_hash,
    )
    raise BlueprintHashDriftError(
        session_id=session_id,
        case_id=case_id,
        case_version=case_version,
        stamped_hash=stamped_hash,
        published_hash=published_hash,
    )
