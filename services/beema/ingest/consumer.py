from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from services.beema.analytics.error_dna import ErrorDNAUpdater
from services.beema.analytics.skill_decay import SkillDecaySignal, SkillDecayUpdater
from services.beema.ingest.freshness_publisher import FreshnessBridge
from services.beema.ledger.interface import (
    ConfirmationState,
    LedgerQuery,
    LedgerReader,
    SessionLedgerRecord,
)
from services.beema.state.cursor_store import CursorStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeemaIngestConfig:
    cohort_ids: tuple[str, ...]
    beema_trusted_physio_versions: frozenset[str]
    accept_unconfirmed: bool = False  # research mode only
    page_size: int = 500


class BeemaIngestor:
    def __init__(
        self,
        reader: LedgerReader,
        cursor_store: CursorStore,
        skill_decay: SkillDecayUpdater,
        error_dna: ErrorDNAUpdater,
        cfg: BeemaIngestConfig,
        *,
        freshness_bridge: Optional[FreshnessBridge] = None,
    ) -> None:
        self.reader = reader
        self.cursor_store = cursor_store
        self.skill_decay = skill_decay
        self.error_dna = error_dna
        self.cfg = cfg
        self.freshness_bridge = freshness_bridge
        if freshness_bridge is not None:
            freshness_bridge.store = skill_decay.store
            existing = skill_decay.on_signal

            def _emit(sig: SkillDecaySignal) -> None:
                freshness_bridge.on_signal(sig)
                if existing is not None:
                    existing(sig)

            skill_decay.on_signal = _emit

    def _trusted_versions(self) -> tuple[str, ...]:
        pratibimb_trusted = self.reader.trusted_physio_versions()
        joint = pratibimb_trusted & self.cfg.beema_trusted_physio_versions
        return tuple(sorted(joint))

    def _accept(self, r: SessionLedgerRecord) -> bool:
        if r.confirmation == ConfirmationState.DISPUTED:
            return False
        if not self.cfg.accept_unconfirmed and r.confirmation == ConfirmationState.UNCONFIRMED:
            return False
        return True

    def run_once(self) -> int:
        trusted = self._trusted_versions()
        if not trusted:
            return 0

        total = 0
        for cohort in self.cfg.cohort_ids:
            cursor_key = f"beema:ingest:{cohort}"
            cursor = self.cursor_store.get(cursor_key)
            q = LedgerQuery(
                cohort_id=cohort,
                physio_engine_version_in=trusted,
                limit=self.cfg.page_size,
                cursor=cursor,
            )

            last_successful: Optional[str] = cursor
            for rec in self.reader.iter_query(q):
                if not self._accept(rec):
                    continue

                ok = self._process(rec)
                if not ok:
                    # Stop without persisting cursor past the last successful
                    # record. The failing record will be replayed.
                    break

                last_successful = rec.session_id
                total += 1

            # Persist pagination cursor (only after all accepted records succeed)
            if last_successful and last_successful != cursor:
                self.cursor_store.set(cursor_key, last_successful)
        return total

    def _process(self, rec: SessionLedgerRecord) -> bool:
        try:
            self.skill_decay.update_from_session(rec)
            self.error_dna.update_from_session(rec)
            return True
        except Exception:
            log.exception("failed processing session_id=%s", rec.session_id)
            return False


def run_forever(ingestor: BeemaIngestor, interval_s: float = 30.0) -> None:
    while True:
        n = ingestor.run_once()
        log.info("beema.ingest.tick processed=%d", n)
        time.sleep(interval_s)

