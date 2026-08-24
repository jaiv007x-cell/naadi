"""
Case sampling for session create.

Reads ``runtime.published_case_corpus`` when a tenant is in play.
Seed JSON is a bootstrap-only bypass (never written into corpus).
"""
from __future__ import annotations

import json
import os
import random
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.pratibimb.app.case_gen.envelope import case_blueprint_from_envelope_json
from services.pratibimb.ledger.models import PublishedCaseCorpusRow
from shared.schemas.case import CaseBlueprint

CORPUS_PATH = Path(__file__).parent / "seed_corpus.json"

BLUEPRINT_SOURCE_PUBLISHED = "published"
BLUEPRINT_SOURCE_SEED = "seed"


def allow_seed_fallback_from_env() -> bool:
    return os.getenv("ALLOW_SEED_FALLBACK", "true").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class SampledCase:
    case: CaseBlueprint
    blueprint_source: str | None
    blueprint_content_hash: str | None
    case_version: str | None
    corpus_envelope_json: str | None


class EmptyCorpusError(RuntimeError):
    """Tenant has no sampleable corpus rows and seed fallback is disabled."""


class ProbeCaseNotFoundError(LookupError):
    """Probe ``case_id`` not found — distinct from ``EmptyCorpusError`` (sample pool)."""


def tenant_projected_count(corpus_session: Session, tenant_id: str) -> int:
    return int(
        corpus_session.scalar(
            select(func.count())
            .select_from(PublishedCaseCorpusRow)
            .where(PublishedCaseCorpusRow.tenant_id == tenant_id)
        )
        or 0
    )


def _load_seed_probe_case(case_id: str) -> CaseBlueprint:
    """Harness/CI seed probe loader — I-E25-7; spy target for regression tests."""
    with CORPUS_PATH.open(encoding="utf-8") as f:
        raw: list[dict] = json.load(f)
    for entry in raw:
        if entry.get("probe_only") and entry.get("case_id") == case_id:
            return CaseBlueprint(**entry)
    raise ProbeCaseNotFoundError(f"Probe case {case_id!r} not found in seed corpus")


def _load_corpus_probe_case(
    corpus_session: Session,
    *,
    tenant_id: str,
    case_id: str,
) -> CaseBlueprint:
    """I-E25-1 / I-E25-3: ``probe_only=true`` is a hardcoded SQL predicate."""
    rows = list(
        corpus_session.scalars(
            select(PublishedCaseCorpusRow)
            .where(
                PublishedCaseCorpusRow.tenant_id == tenant_id,
                PublishedCaseCorpusRow.case_id == case_id,
                PublishedCaseCorpusRow.probe_only.is_(True),
            )
            .order_by(
                PublishedCaseCorpusRow.published_at.desc(),
                PublishedCaseCorpusRow.version.desc(),
            )
        )
    )
    if not rows:
        raise ProbeCaseNotFoundError(
            f"probe case {case_id!r} not found in corpus for tenant={tenant_id!r}"
        )
    return case_blueprint_from_envelope_json(rows[0].envelope_json)


def load_probe_case(
    case_id: str,
    *,
    tenant_id: str | None = None,
    corpus_session: Session | None = None,
    harness: bool = False,
    allow_seed_fallback: bool | None = None,
) -> CaseBlueprint:
    """
    Load a probe-only ``CaseBlueprint`` — I-E25 invariants (phase_e.md §E2.5).

    Runtime callers must pass ``tenant_id`` + ``corpus_session``.
    Harness/CI (`version_probe`) passes ``harness=True`` with no tenant.
    """
    if tenant_id is None:
        if not harness:
            raise ValueError(
                "load_probe_case requires tenant_id for runtime callers, "
                "or harness=True for CI/harness seed path (I-E25-7)"
            )
        return _load_seed_probe_case(case_id)

    if corpus_session is None:
        raise ValueError("corpus_session is required when tenant_id is set")

    allow = (
        allow_seed_fallback_from_env()
        if allow_seed_fallback is None
        else allow_seed_fallback
    )
    if tenant_projected_count(corpus_session, tenant_id) > 0:
        return _load_corpus_probe_case(
            corpus_session,
            tenant_id=tenant_id,
            case_id=case_id,
        )

    if allow:
        return _load_seed_probe_case(case_id)
    raise ProbeCaseNotFoundError(
        f"no projected corpus rows for tenant={tenant_id!r} and seed fallback disabled"
    )


class CaseSampler:
    """
    Session-start case selection only — not a projection-integrity / QA API.

    Pool when tenant has ≥1 projected corpus row: ``NOT probe_only`` for that
    tenant. Seed is unreachable for that tenant forever (one-way flip), even if
    ``ALLOW_SEED_FALLBACK`` remains true.
    """

    def __init__(
        self,
        *,
        corpus_session: Session | None = None,
        allow_seed_fallback: bool | None = None,
    ) -> None:
        self._corpus_session = corpus_session
        self._allow_seed_fallback = (
            allow_seed_fallback_from_env()
            if allow_seed_fallback is None
            else allow_seed_fallback
        )
        with CORPUS_PATH.open(encoding="utf-8") as f:
            raw: list[dict] = json.load(f)
        self._seed: list[dict] = [c for c in raw if not c.get("probe_only")]

    def tenant_projected_count(self, tenant_id: str) -> int:
        if self._corpus_session is None:
            return 0
        return tenant_projected_count(self._corpus_session, tenant_id)

    def sample(
        self,
        *,
        tenant_id: str | None = None,
        learner_gaps: dict[str, float] | None = None,
        target_difficulty: float = 0.5,
        state: str | None = None,
    ) -> CaseBlueprint:
        """Backward-compatible: returns the CaseBlueprint only."""
        return self.sample_with_provenance(
            tenant_id=tenant_id,
            learner_gaps=learner_gaps,
            target_difficulty=target_difficulty,
            state=state,
        ).case

    def sample_with_provenance(
        self,
        *,
        tenant_id: str | None = None,
        learner_gaps: dict[str, float] | None = None,
        target_difficulty: float = 0.5,
        state: str | None = None,
    ) -> SampledCase:
        if tenant_id is not None and self._corpus_session is not None:
            if self.tenant_projected_count(tenant_id) > 0:
                return self._sample_corpus(
                    tenant_id=tenant_id,
                    learner_gaps=learner_gaps,
                    target_difficulty=target_difficulty,
                    state=state,
                )
            if self._allow_seed_fallback:
                return self._sample_seed(
                    learner_gaps=learner_gaps,
                    target_difficulty=target_difficulty,
                    state=state,
                    blueprint_source=BLUEPRINT_SOURCE_SEED,
                )
            raise EmptyCorpusError(
                f"no projected corpus rows for tenant={tenant_id} and "
                "ALLOW_SEED_FALLBACK is false"
            )

        # Legacy / no corpus wiring: seed with null provenance (pre-E2.2 shape).
        return self._sample_seed(
            learner_gaps=learner_gaps,
            target_difficulty=target_difficulty,
            state=state,
            blueprint_source=None,
        )

    def _sample_corpus(
        self,
        *,
        tenant_id: str,
        learner_gaps: dict[str, float] | None,
        target_difficulty: float,
        state: str | None,
    ) -> SampledCase:
        rows = list(
            self._corpus_session.scalars(  # type: ignore[union-attr]
                select(PublishedCaseCorpusRow).where(
                    PublishedCaseCorpusRow.tenant_id == tenant_id,
                    PublishedCaseCorpusRow.probe_only.is_(False),
                )
            )
        )
        if not rows:
            raise EmptyCorpusError(
                f"tenant={tenant_id} has projected rows but none eligible "
                "(all probe_only); seed is unreachable after first projection"
            )

        def score(row: PublishedCaseCorpusRow) -> float:
            case = case_blueprint_from_envelope_json(row.envelope_json)
            d = 1.0 - abs(case.difficulty - target_difficulty)
            gap_boost = 0.0
            if learner_gaps:
                for tag in case.competency_tags:
                    gap_boost += max(0.0, 1.0 - learner_gaps.get(tag, 1.0))
            if state and case.demographics.state.lower() != state.lower():
                return 0.01
            return d + 0.5 * gap_boost

        weights = [score(r) for r in rows]
        chosen = random.choices(rows, weights=weights, k=1)[0]
        case = case_blueprint_from_envelope_json(chosen.envelope_json)
        return SampledCase(
            case=case,
            blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
            blueprint_content_hash=chosen.content_hash,
            case_version=chosen.version,
            corpus_envelope_json=chosen.envelope_json,
        )

    def _sample_seed(
        self,
        *,
        learner_gaps: dict[str, float] | None,
        target_difficulty: float,
        state: str | None,
        blueprint_source: str | None,
    ) -> SampledCase:
        pool = self._seed
        if state:
            pool = [
                c
                for c in pool
                if c["demographics"]["state"].lower() == state.lower()
            ] or self._seed

        def score(c: dict) -> float:
            d = 1.0 - abs(c["difficulty"] - target_difficulty)
            gap_boost = 0.0
            if learner_gaps:
                for tag in c.get("competency_tags", []):
                    gap_boost += max(0.0, 1.0 - learner_gaps.get(tag, 1.0))
            return d + 0.5 * gap_boost

        weights = [score(c) for c in pool]
        chosen = dict(random.choices(pool, weights=weights, k=1)[0])
        chosen["case_id"] = f"PRB-{uuid.uuid4().hex[:10]}"
        return SampledCase(
            case=CaseBlueprint(**chosen),
            blueprint_source=blueprint_source,
            blueprint_content_hash=None,
            case_version=None,
            corpus_envelope_json=None,
        )
