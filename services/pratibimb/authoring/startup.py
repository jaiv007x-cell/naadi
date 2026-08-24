"""Authoring harness startup — schema, migrations, corpus seed."""
from __future__ import annotations

import logging

from services.pratibimb.authoring.db import authoring_session, get_authoring_engine
from services.pratibimb.authoring.migrations.runner import apply_pending_migrations
from services.pratibimb.authoring.seed import ensure_ramesh_smoke_wired, seed_ramesh_draft

log = logging.getLogger(__name__)


def validate_authoring_startup() -> None:
    """Apply authoring migrations and seed the Ramesh draft if absent."""
    engine = get_authoring_engine()
    applied = apply_pending_migrations(engine)
    if applied:
        log.info("authoring.migrations_applied versions=%s", applied)

    with authoring_session() as session:
        created = seed_ramesh_draft(session)
        if created:
            log.info("authoring.startup_seeded draft_id=%s", created)
        elif ensure_ramesh_smoke_wired(session):
            log.info(
                "authoring.startup_wired_ramesh_smoke draft_id=ramesh-kale-stemi-inferior-v1"
            )