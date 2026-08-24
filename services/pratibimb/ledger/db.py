"""

Ledger database bootstrap.



Uses LEDGER_DATABASE_URL when set; otherwise ledger append is skipped unless

a session is injected explicitly by tests or callers.



Trust policy (Evidence Ledger v1):

  - dev/test: auto-seed the running physio version for frictionless local work

  - production: schema only; operator must explicitly promote versions

"""

from __future__ import annotations



import logging

import os

from contextlib import contextmanager

from functools import lru_cache

from typing import Iterator



from sqlalchemy import create_engine

from sqlalchemy.engine import Engine

from sqlalchemy.orm import Session, sessionmaker



from services.pratibimb.app.physio.version_probe import get_version

from services.pratibimb.ledger.models import Base, TrustedPhysioVersion

from services.pratibimb.ledger.namespace import ensure_runtime_namespace




log = logging.getLogger(__name__)



LEDGER_DATABASE_URL_ENV = "LEDGER_DATABASE_URL"

DEFAULT_LEDGER_URL = "sqlite:///./pratibimb_ledger.db"





def is_dev_environment() -> bool:

    if os.getenv("PYTEST_CURRENT_TEST"):

        return True

    env = os.getenv("PRATIBIMB_ENV", os.getenv("ENV", "development")).lower()

    return env in {"dev", "development", "local", "test"}





@lru_cache

def get_engine(url: str | None = None) -> Engine:

    db_url = url or os.getenv(LEDGER_DATABASE_URL_ENV, DEFAULT_LEDGER_URL)

    return create_engine(db_url, future=True)





def init_ledger_schema(

    engine: Engine | None = None,

    *,

    auto_seed_trust: bool | None = None,

) -> None:

    eng = engine or get_engine()

    ensure_runtime_namespace(eng)

    Base.metadata.create_all(eng)

    should_seed = is_dev_environment() if auto_seed_trust is None else auto_seed_trust

    if should_seed:

        seed_trusted_physio_version(eng)

    elif not is_dev_environment():

        running = get_version()

        log.info(

            "ledger trust gate: production boot — physio version %s is NOT "

            "auto-trusted; promote via promote_physio_version() after validation",

            running,

        )





def seed_trusted_physio_version(engine: Engine, *, approved_by: str = "system") -> None:

    """Dev/test helper: trust the currently running physio engine version."""

    promote_physio_version(

        engine,

        get_version(),

        approved_by=approved_by,

        notes="Auto-seeded in dev/test from version_probe.get_version()",

    )





def promote_physio_version(

    engine: Engine,

    version: str,

    *,

    approved_by: str,

    notes: str | None = None,

) -> None:

    """

    Operator-approved promotion after validation/replay suite.



    Idempotent: re-promoting an already-trusted version is a no-op.

    """

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with SessionLocal() as session:

        if session.get(TrustedPhysioVersion, version) is not None:

            return

        session.add(

            TrustedPhysioVersion(

                version=version,

                approved_by=approved_by,

                notes=notes,

            )

        )

        session.commit()

        log.info(

            "ledger trust gate: promoted physio version %s (approved_by=%s)",

            version,

            approved_by,

        )





@contextmanager

def ledger_session(url: str | None = None) -> Iterator[Session]:

    eng = get_engine(url)

    init_ledger_schema(eng)

    SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    session = SessionLocal()

    try:

        yield session

    finally:

        session.close()





@lru_cache
def get_audit_engine(url: str | None = None) -> Engine:
    """Separate engine/pool for audit writes — isolated from read sessions."""
    db_url = url or os.getenv(LEDGER_DATABASE_URL_ENV, DEFAULT_LEDGER_URL)
    pool_size = int(os.getenv("LEDGER_AUDIT_POOL_SIZE", "5"))
    return create_engine(db_url, future=True, pool_size=pool_size)


def ledger_enabled() -> bool:
    return os.getenv("LEDGER_DISABLED", "").lower() not in {"1", "true", "yes"}

