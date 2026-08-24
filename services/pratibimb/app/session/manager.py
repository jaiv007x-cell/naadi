from __future__ import annotations

import time
import uuid
from datetime import datetime

from shared.schemas.case import Language
from shared.schemas.scoring import SessionScoreReport
from shared.schemas.session import (
    ActionRecord,
    AssessmentMode,
    ClinicalActionRequest,
    CreateSessionRequest,
    DialogueResponse,
    PatientResponsePayload,
    SessionResponse,
    SessionState,
    SessionStatus,
    StudentUtteranceRequest,
    TranscriptTurn,
)

from services.pratibimb.app.case_gen.envelope import case_blueprint_from_envelope_json
from services.pratibimb.app.case_gen.sampler import CaseSampler, SampledCase
from services.pratibimb.app.dhaara_client import DhaaraClient
from services.pratibimb.app.eval.nirikshak import MissingGradingBlueprint
from services.pratibimb.app.eval.scorer import Scorer, build_turns, grade_case
from services.pratibimb.app.persona.emotion import EmotionState
from services.pratibimb.app.persona.llm import PersonaLLM
from services.pratibimb.app.physio.state import PhysioTrace, PhysiologyEngine
from services.pratibimb.app.session.events import SessionEvent, event_payload
from services.pratibimb.app.speech.tts import get_tts_provider
from services.pratibimb.ledger.db import ledger_enabled, ledger_session
from services.pratibimb.ledger.writer import (
    BLUEPRINT_SOURCE_PUBLISHED,
    LedgerConflictError,
    append_graded_session,
)
from shared.logging import get_logger

log = get_logger(__name__)


class SessionManager:
    """In-memory session state machine for MVP."""

    def __init__(
        self,
        *,
        authoring_session=None,
        corpus_session=None,
        allow_seed_fallback: bool | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._engines: dict[str, PhysiologyEngine] = {}
        self._emotions: dict[str, EmotionState] = {}
        self._histories: dict[str, list[dict]] = {}
        self._sampler = CaseSampler(
            corpus_session=corpus_session,
            allow_seed_fallback=allow_seed_fallback,
        )
        self._persona = PersonaLLM()
        self._scorer = Scorer()
        self._dhaara = DhaaraClient()
        self._tts = get_tts_provider()
        self._events: list[dict] = []
        # Phase E1: optional authoring session for published-case stamp/verify.
        self._authoring_session = authoring_session
        # Phase E2.2.d/e: optional corpus session for project_on_miss + envelope load.
        self._corpus_session = corpus_session

    def create_session(self, req: CreateSessionRequest) -> SessionResponse:
        explicit_pick = req.case_id is not None or req.case_version is not None
        if explicit_pick and not all(
            v is not None for v in (req.tenant_id, req.case_id, req.case_version)
        ):
            raise ValueError(
                "tenant_id, case_id, and case_version must be provided together "
                "for published-backed session create"
            )

        stamped_hash: str | None = None
        blueprint_source: str | None = None
        case_version: str | None = None
        tenant_id: str | None = req.tenant_id
        corpus_envelope_json: str | None = None
        case = None

        if explicit_pick:
            if self._authoring_session is None:
                raise ValueError(
                    "authoring_session is required for published-backed session create"
                )
            case_version = req.case_version
            picked = self._load_published_pick(
                tenant_id=req.tenant_id,  # type: ignore[arg-type]
                case_id=req.case_id,  # type: ignore[arg-type]
                case_version=req.case_version,  # type: ignore[arg-type]
            )
            stamped_hash = picked.blueprint_content_hash
            blueprint_source = picked.blueprint_source
            corpus_envelope_json = picked.corpus_envelope_json
            case = picked.case
        else:
            sampled: SampledCase = self._sampler.sample_with_provenance(
                tenant_id=req.tenant_id,
                learner_gaps=req.learner_gaps,
                target_difficulty=req.target_difficulty,
                state=req.state,
            )
            case = sampled.case
            stamped_hash = sampled.blueprint_content_hash
            blueprint_source = sampled.blueprint_source
            case_version = sampled.case_version
            corpus_envelope_json = sampled.corpus_envelope_json
            if tenant_id is None and sampled.blueprint_source == BLUEPRINT_SOURCE_PUBLISHED:
                # Corpus sample always implies a tenant was provided.
                tenant_id = req.tenant_id

        session_id = f"SES-{uuid.uuid4().hex[:12]}"
        lang = req.language or case.demographics.native_language

        state = SessionState(
            session_id=session_id,
            learner_id=req.learner_id,
            case=case,
            status=SessionStatus.CREATED,
            assessment_mode=req.assessment_mode,
            language=lang,
            current_vitals=case.baseline_vitals.model_copy(),
            tenant_id=tenant_id,
            case_version=case_version,
            blueprint_content_hash=stamped_hash,
            blueprint_source=blueprint_source,
            corpus_envelope_json=corpus_envelope_json,
        )
        self._sessions[session_id] = state
        self._engines[session_id] = PhysiologyEngine(case)
        self._emotions[session_id] = EmotionState.from_case(case)
        self._histories[session_id] = []

        self._emit(SessionEvent.CREATED, session_id, {"case_id": case.case_id})
        return self._to_response(state)

    def _load_published_pick(
        self,
        *,
        tenant_id: str,
        case_id: str,
        case_version: str,
    ) -> SampledCase:
        """Load case + stamp for an explicit published pick (corpus when wired)."""
        if self._corpus_session is not None:
            from services.pratibimb.ledger.corpus_projector import CorpusProjector

            projector = CorpusProjector(self._authoring_session, self._corpus_session)
            corpus_row = projector.get_by_triple(
                tenant_id=tenant_id,
                case_id=case_id,
                version=case_version,
            )
            if corpus_row is None:
                # I-E22-5 / E2.2.d — sole sync runtime→projector call site.
                # Explicit-pick identity semantics require the corpus to heal
                # synchronously for a case known to exist in authoring: the
                # post-commit hook may not have projected yet when a learner
                # starts the case. Hook + reconciler remain the authoritative
                # write path; do NOT add another project_on_miss call site —
                # if the pipeline is lagging, fix the pipeline, don't sprinkle
                # sync heals.
                corpus_row = projector.project_on_miss(
                    tenant_id=tenant_id,
                    case_id=case_id,
                    version=case_version,
                )
            if corpus_row is None:
                from services.pratibimb.authoring.errors import (
                    PublishedCaseNotFoundError,
                )

                raise PublishedCaseNotFoundError(
                    tenant_id=tenant_id,
                    case_id=case_id,
                    case_version=case_version,
                )
            # E2.2.e: envelope is the runtime read surface — deserialize these bytes.
            envelope_text = corpus_row.envelope_json
            case = case_blueprint_from_envelope_json(envelope_text)
            return SampledCase(
                case=case,
                blueprint_source=BLUEPRINT_SOURCE_PUBLISHED,
                blueprint_content_hash=corpus_row.content_hash,
                case_version=corpus_row.version,
                corpus_envelope_json=envelope_text,
            )

        # ------------------------------------------------------------------
        # Legacy hatch (E1 transitional / E2.4.b) — DO NOT ADD CALLERS.
        # Path: explicit pick with authoring_session but no corpus_session →
        # (blueprint_content_hash, blueprint_source=NULL) + seed physio overwrite.
        # Observable: ledger_legacy_hatch_total{tenant} — must trend to zero.
        # Grep that metric name to find this site; grep this comment to find
        # record_ledger_legacy_hatch. Removal blast radius: E1 resolver callers.
        # See docs/design/authoring_harness_phase_e.md § E2.4 (I-E24-3).
        # ------------------------------------------------------------------
        from services.pratibimb.authoring.metrics import record_ledger_legacy_hatch
        from services.pratibimb.ledger.blueprint_hash import (
            resolve_published_content_hash,
        )

        record_ledger_legacy_hatch(tenant_id=tenant_id)
        stamped = resolve_published_content_hash(
            self._authoring_session,
            tenant_id=tenant_id,
            case_id=case_id,
            case_version=case_version,
        )
        # Physio shape still from seed until corpus is wired (legacy tests).
        sampled = self._sampler.sample_with_provenance()
        case = sampled.case.model_copy(update={"case_id": case_id})
        return SampledCase(
            case=case,
            blueprint_source=None,
            blueprint_content_hash=stamped,
            case_version=case_version,
            corpus_envelope_json=None,
        )

    def start_session(self, session_id: str) -> SessionResponse:
        state = self._require(session_id)
        state.status = SessionStatus.ACTIVE
        state.started_at = datetime.utcnow()
        engine = self._engines[session_id]
        engine.state.started_at = time.time()

        complaint = state.case.chief_complaint_verbatim.get(
            state.language,
            state.case.chief_complaint_verbatim.get(state.case.demographics.native_language, ""),
        )
        state.transcript.append(
            TranscriptTurn(role="patient", content=complaint, language=state.language)
        )
        self._emit(SessionEvent.STARTED, session_id)
        return self._to_response(state)

    async def handle_utterance(
        self, session_id: str, req: StudentUtteranceRequest
    ) -> DialogueResponse:
        state = self._require(session_id)
        self._ensure_active(state)

        lang = req.language or state.language
        state.transcript.append(
            TranscriptTurn(role="student", content=req.utterance, language=lang)
        )

        emotion = self._emotions[session_id]
        empathy = self._estimate_empathy(req.utterance)
        jargon = self._estimate_jargon(req.utterance)
        emotion = emotion.update_from_student(empathy, jargon)
        self._emotions[session_id] = emotion

        history = self._histories[session_id]
        persona_out = await self._persona.respond(
            state.case, emotion, history, req.utterance, lang
        )

        utterance = persona_out.get("utterance", "...")
        history.append({"role": "user", "content": req.utterance})
        history.append({"role": "assistant", "content": utterance})

        state.transcript.append(
            TranscriptTurn(
                role="patient",
                content=utterance,
                language=lang,
                metadata={"prosody": persona_out.get("prosody", {})},
            )
        )

        engine = self._engines[session_id]
        elapsed = time.time() - engine.state.started_at
        vitals = engine.tick(seconds=1.0)
        state.current_vitals = vitals
        state.elapsed_seconds = elapsed

        audio = await self._tts.synthesize(utterance, lang)

        self._emit(SessionEvent.STUDENT_UTTERANCE, session_id, {"utterance": req.utterance})
        self._emit(SessionEvent.PATIENT_RESPONSE, session_id, {"utterance": utterance})

        return DialogueResponse(
            patient=PatientResponsePayload(
                utterance=utterance,
                prosody=persona_out.get("prosody", {}),
                comprehension=persona_out.get("comprehension", emotion.comprehension),
                family_interjection=persona_out.get("family_interjection"),
                audio_url=None if audio is None else "inline://stub",
            ),
            vitals=vitals,
            elapsed_seconds=elapsed,
            session_status=state.status,
        )

    async def apply_action(
        self, session_id: str, req: ClinicalActionRequest
    ) -> dict:
        state = self._require(session_id)
        self._ensure_active(state)

        engine = self._engines[session_id]
        result = engine.apply_action(req.action, req.params)
        elapsed = time.time() - engine.state.started_at

        record = ActionRecord(
            action=req.action,
            t=elapsed,
            params=req.params,
            result=result,
        )
        state.action_log.append(record)
        state.current_vitals = engine.state.vitals
        state.elapsed_seconds = elapsed

        state.transcript.append(
            TranscriptTurn(
                role="system",
                content=f"Action: {req.action} → {result.get('observed', '')}",
                metadata=result,
            )
        )

        self._emit(SessionEvent.CLINICAL_ACTION, session_id, record.model_dump())
        return {"action": req.action, "result": result, "vitals": state.current_vitals}

    async def complete_session(self, session_id: str) -> SessionScoreReport:
        state = self._require(session_id)
        engine = self._engines[session_id]

        action_log = [{"action": a.action, "t": a.t} for a in state.action_log]
        transcript = [{"role": t.role, "content": t.content} for t in state.transcript]
        stress_delta = -1.0 if engine.state.dead else 0.5

        # Fail closed on the REST path too: a summative session must not be able
        # to complete with a scorecard that skipped rubric grading entirely.
        blueprint = getattr(state.case, "grading_blueprint", None)
        mode = AssessmentMode.strictest(
            state.assessment_mode,
            getattr(state.case, "assessment_mode", None),
        )
        if blueprint is None and mode.requires_grade:
            raise MissingGradingBlueprint(
                session_id, state.case.case_id, getattr(state.case, "version", ""),
            )

        rubric = await self._scorer.score(
            state.case,
            action_log,
            transcript,
            stress_delta,
            physio=PhysioTrace.from_engine(engine),
            blueprint=blueprint,
        )
        actions_taken = [a.action for a in state.action_log]
        crit_hit = [c for c in state.case.critical_actions if c in actions_taken]
        crit_missed = [c for c in state.case.critical_actions if c not in actions_taken]

        updates = self._scorer.competency_updates(state.case, rubric, actions_taken)
        for u in updates:
            u.learner_id = state.learner_id

        report = SessionScoreReport(
            session_id=session_id,
            learner_id=state.learner_id,
            case_id=state.case.case_id,
            rubric=rubric,
            critical_actions_hit=crit_hit,
            critical_actions_missed=crit_missed,
            competency_updates=updates,
        )

        state.status = SessionStatus.COMPLETED
        state.completed_at = datetime.utcnow()

        await self._dhaara.push_competency_updates(report)
        await self._dhaara.emit_session_event(
            session_id,
            "session.scored",
            {"overall": rubric.overall, "scorecard": report.model_dump()},
        )
        self._emit(SessionEvent.COMPLETED, session_id)
        self._emit(SessionEvent.SCORED, session_id, {"overall": rubric.overall})

        if blueprint is not None and ledger_enabled():
            turns = build_turns(action_log, transcript, state.case.patient_lang)
            physio = PhysioTrace.from_engine(engine)
            try:
                grade, _migration = grade_case(
                    blueprint, physio, turns, case_id=state.case.case_id
                )
                case_version = (
                    state.case_version
                    or getattr(state.case, "version", None)
                    or blueprint.case_version
                )
                with ledger_session() as db:
                    append_graded_session(
                        db,
                        session_id=session_id,
                        learner_pseudo_id=state.learner_id,
                        cohort_id=getattr(state, "cohort_id", "default"),
                        case_id=state.case.case_id,
                        case_version=case_version,
                        grade=grade,
                        blueprint=blueprint,
                        legacy_trace=physio,
                        turns=turns,
                        blueprint_content_hash=state.blueprint_content_hash,
                        blueprint_source=state.blueprint_source,
                        tenant_id=state.tenant_id,
                        authoring_session=self._authoring_session,
                    )
            except LedgerConflictError:
                log.exception("ledger conflict on session=%s", session_id)
                raise

        return report

    def get_session(self, session_id: str) -> SessionResponse:
        return self._to_response(self._require(session_id))

    def get_events(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            return [e for e in self._events if e.get("session_id") == session_id]
        return list(self._events)

    def _require(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return self._sessions[session_id]

    def _ensure_active(self, state: SessionState) -> None:
        if state.status != SessionStatus.ACTIVE:
            raise ValueError(f"Session {state.session_id} is not active (status={state.status})")

    def _to_response(self, state: SessionState) -> SessionResponse:
        complaint = state.case.chief_complaint_verbatim.get(
            state.language,
            state.case.chief_complaint_verbatim.get(state.case.demographics.native_language, ""),
        )
        return SessionResponse(
            session_id=state.session_id,
            status=state.status,
            case_id=state.case.case_id,
            patient_name=state.case.demographics.name,
            chief_complaint=complaint,
            language=state.language,
            vitals=state.current_vitals,
            elapsed_seconds=state.elapsed_seconds,
            time_pressure_seconds=state.case.time_pressure_seconds,
            family_present=state.case.family_present,
            resources_available=state.case.resources_available,
        )

    def _emit(self, event: SessionEvent, session_id: str, data: dict | None = None) -> None:
        payload = event_payload(event, session_id, data)
        self._events.append(payload)
        log.info("session_event", event_type=payload["event_type"], session_id=payload["session_id"], data=payload.get("data"))

    @staticmethod
    def _estimate_empathy(text: str) -> float:
        keywords = ["namaste", "kasa", "kaise", "sorry", "samjha", "help", "madad", "aaram"]
        lower = text.lower()
        hits = sum(1 for k in keywords if k in lower)
        return min(1.0, hits * 0.3 + (0.2 if len(text) > 20 else 0))

    @staticmethod
    def _estimate_jargon(text: str) -> float:
        jargon = ["myocardial", "infarction", "troponin", "stemi", "ischemia", "arrhythmia"]
        lower = text.lower()
        hits = sum(1 for j in jargon if j in lower)
        return min(1.0, hits * 0.4 + (0.3 if sum(1 for c in text if c.isascii()) / max(len(text), 1) > 0.9 else 0))
