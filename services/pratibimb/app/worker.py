"""
Pratibimb event worker — the actual loop that `python -m app.worker` runs.
Pulls events off Dhaara (Redis Streams), routes them through:
    ASR → PersonaLLM → TTS → Physio → Scorer
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from dataclasses import asdict

from services.pratibimb.app.case_gen.sampler import CaseSampler
from services.pratibimb.app.dhaara_client import DhaaraClient, STREAM_IN, STREAM_OUT
from services.pratibimb.app.eval.scorer import Turn, grade_case, score_session
from services.pratibimb.app.eval.nirikshak import (
    CaseGrade,
    MissingGradingBlueprint,
    UngradableSessionError,
)
from services.pratibimb.app.eval.rubric import GradingBlueprint
from services.pratibimb.app.persona.emotion import EmotionState, update_emotion
from services.pratibimb.app.persona.llm import PersonaLLM
from services.pratibimb.app.physio.state import PhysiologyEngine, PhysioTrace
from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.app.speech.asr import CodeSwitchASR, StubASR, get_asr_provider
from services.pratibimb.app.speech.tts import VernacularTTS, StubTTS, get_tts_provider
from services.pratibimb.ledger.db import ledger_enabled, ledger_session
from services.pratibimb.ledger.startup import validate_ledger_startup
from services.pratibimb.ledger.writer import LedgerConflictError, append_graded_session
from shared.schemas.case import CaseBlueprint, Language
from shared.schemas.migration import MigrationReport
from shared.schemas.session import (
    AssessmentMode,
    ClinicalActionRequest,
    CreateSessionRequest,
    StudentUtteranceRequest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("pratibimb.worker")

SPEAKER_REF_DIR = os.getenv("SPEAKER_REF_DIR", "/app/speaker_refs")


class Session:
    __slots__ = (
        "id", "case", "physio", "emotion", "history", "turns", "started_at",
        "patient_wav", "grade", "assessment_mode", "trace_migration",
        "learner_id", "cohort_id", "ledger_replay_hash",
    )

    def __init__(
        self,
        sid: str,
        case: CaseBlueprint,
        speaker_wav: str,
        assessment_mode: AssessmentMode = AssessmentMode.PRACTICE,
        *,
        learner_id: str = "",
        cohort_id: str = "default",
    ):
        self.id = sid
        self.case = case
        self.physio = PhysiologyEngine(case)
        self.emotion = EmotionState.from_case(case)
        self.history: list[dict] = []
        self.turns: list[Turn] = []
        self.started_at = time.time()
        self.patient_wav = speaker_wav
        self.assessment_mode = assessment_mode
        self.grade: CaseGrade | None = None
        self.trace_migration: MigrationReport | None = None
        self.learner_id = learner_id or f"anon-{sid[:8]}"
        self.cohort_id = cohort_id
        self.ledger_replay_hash: str | None = None

    @property
    def effective_assessment_mode(self) -> AssessmentMode:
        """Stricter of the case's declared stakes and the session's launch mode."""
        return AssessmentMode.strictest(
            self.assessment_mode,
            getattr(self.case, "assessment_mode", None),
        )

    def finalize(self, blueprint: GradingBlueprint | None = None) -> CaseGrade | None:
        """
        Grade the session with Nirikshak and append to the ledger when enabled.

        Fails closed: a summative session with no usable blueprint raises
        `MissingGradingBlueprint` instead of returning `None`.
        """
        if blueprint is None:
            blueprint = getattr(self.case, "grading_blueprint", None)
        mode = self.effective_assessment_mode

        if blueprint is None:
            if mode.requires_grade:
                raise MissingGradingBlueprint(
                    self.id, self.case.case_id,
                    getattr(self.case, "version", ""),
                )
            log.info(
                "session %s (%s) finalized without a grade: no blueprint",
                self.id, mode.value,
            )
            return None

        try:
            self.grade, self.trace_migration = grade_case(
                blueprint, self.physio.trace(), self.turns, case_id=self.case.case_id
            )
        except UngradableSessionError:
            raise
        except Exception as exc:
            if mode.requires_grade:
                raise UngradableSessionError(
                    self.id, self.case.case_id, f"grader raised {type(exc).__name__}: {exc}"
                ) from exc
            log.exception("grading failed for session %s, continuing ungraded", self.id)
            return None

        if ledger_enabled():
            try:
                with ledger_session() as db:
                    self.ledger_replay_hash = append_graded_session(
                        db,
                        session_id=self.id,
                        learner_pseudo_id=self.learner_id,
                        cohort_id=self.cohort_id,
                        case_id=self.case.case_id,
                        case_version=getattr(self.case, "version", blueprint.case_version),
                        grade=self.grade,
                        blueprint=blueprint,
                        legacy_trace=self.physio.trace(),
                        turns=self.turns,
                    )
            except LedgerConflictError:
                log.exception("ledger conflict on session=%s", self.id)
                raise

        return self.grade


class Worker:
    """Standalone worker — consumes Dhaara events, drives the full pipeline."""

    def __init__(self) -> None:
        self.dhaara = DhaaraClient()
        self.sampler = CaseSampler()
        self.llm = PersonaLLM()
        self.asr = get_asr_provider()
        self.tts = get_tts_provider()
        self.sessions: dict[str, Session] = {}

    async def run(self) -> None:
        await self.dhaara.connect()
        log.info("worker online, consuming stream")
        consumer = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        async for evt in self.dhaara.consume(consumer=consumer):
            try:
                await self._dispatch(evt)
            except Exception:
                log.exception("dispatch failed for evt=%s", evt.get("type"))

    async def _dispatch(self, evt: dict) -> None:
        t = evt.get("type")
        if t == "case.start":
            await self._on_case_start(evt)
        elif t == "audio.chunk":
            await self._on_audio_chunk(evt)
        elif t in ("dialogue", "student.utterance"):
            await self._on_text_utterance(evt)
        elif t in ("learner.action", "clinical.action", "action"):
            await self._on_learner_action(evt)
        elif t == "physio.tick":
            await self._on_physio_tick(evt)
        elif t == "score.request":
            await self._on_score_request(evt)
        elif t == "session.end":
            await self._on_session_end(evt)
        else:
            log.warning("unknown evt type=%s", t)

    async def _on_case_start(self, evt: dict) -> None:
        sid = evt["session_id"]
        case = self.sampler.sample(
            target_difficulty=evt.get("target_difficulty", 0.55),
            state=evt.get("state"),
        )
        speaker_wav = os.path.join(SPEAKER_REF_DIR, f"{case.persona_voice_id}.wav")
        mode = AssessmentMode(evt.get("assessment_mode", AssessmentMode.PRACTICE.value))
        self.sessions[sid] = Session(
            sid,
            case,
            speaker_wav,
            assessment_mode=mode,
            learner_id=evt.get("learner_id", ""),
            cohort_id=evt.get("cohort_id", "default"),
        )
        await self.dhaara.publish(STREAM_OUT, {
            "type": "case.ready",
            "session_id": sid,
            "case_id": case.case_id,
            "briefing": {
                "patient_name": case.demographics.name,
                "chief_complaint": _chief_complaint(case, evt.get("language", "hi")),
                "vitals": case.baseline_vitals.model_dump(),
                "time_pressure_seconds": case.time_pressure_seconds,
                "family_present": case.family_present,
                "resources_available": case.resources_available,
            },
            "language": case.patient_lang,
        })
        asyncio.create_task(self._physio_loop(sid))

    async def _physio_loop(self, sid: str) -> None:
        s = self.sessions.get(sid)
        if not s:
            return
        while sid in self.sessions:
            snap = s.physio.tick(dt=1.0)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "physio.snapshot",
                "session_id": sid,
                "vitals": s.physio.snapshot(),
                "t": time.time() - s.started_at,
            })
            await asyncio.sleep(1.0)

    async def _on_audio_chunk(self, evt: dict) -> None:
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            return
        pcm = base64.b64decode(evt["pcm16_b64"])

        if hasattr(self.asr, "transcribe_segments"):
            segs = self.asr.transcribe_segments(pcm)
            if not segs:
                return
            text = " ".join(x.text for x in segs).strip()
            lang = max({x.lang for x in segs}, key=lambda l: sum(1 for x in segs if x.lang == l))
        else:
            text = "[ASR stub]"
            lang = s.case.patient_lang

        ts = time.time() - s.started_at
        s.turns.append(Turn(speaker="learner", text=text, ts=ts, lang=lang))
        s.emotion = update_emotion(s.emotion, learner_text=text, physio=s.physio.snapshot())

        await self.dhaara.publish(STREAM_OUT, {
            "type": "asr.partial",
            "session_id": sid,
            "text": text,
            "lang": lang,
            "t": ts,
        })

        await self._generate_persona_reply(s, text, lang)

    async def _on_text_utterance(self, evt: dict) -> None:
        """Handle text-based dialogue (no audio)."""
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            return
        text = evt.get("utterance", evt.get("text", ""))
        lang = evt.get("language", s.case.patient_lang)
        ts = time.time() - s.started_at
        s.turns.append(Turn(speaker="learner", text=text, ts=ts, lang=lang))
        s.emotion = update_emotion(s.emotion, learner_text=text, physio=s.physio.snapshot())

        await self._generate_persona_reply(s, text, lang)

    async def _generate_persona_reply(self, s: Session, text: str, lang: str) -> None:
        full: list[str] = []
        async for tok in self.llm.reply(s.case, s.emotion, s.history, text, lang):
            full.append(tok)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "persona.token",
                "session_id": s.id,
                "delta": tok,
            })

        patient_text = "".join(full).strip()
        if patient_text.endswith("</patient>"):
            patient_text = patient_text[: -len("</patient>")].strip()

        s.history.append({"role": "user", "content": text})
        s.history.append({"role": "assistant", "content": patient_text})
        s.turns.append(
            Turn(
                speaker="patient",
                text=patient_text,
                ts=time.time() - s.started_at,
                lang=s.case.patient_lang,
            )
        )

        await self.dhaara.publish(STREAM_OUT, {
            "type": "patient.response",
            "session_id": s.id,
            "text": patient_text,
        })

        # TTS
        try:
            if hasattr(self.tts, "synth"):
                wav = self.tts.synth(patient_text, lang=s.case.patient_lang, speaker_wav=s.patient_wav)
                await self.dhaara.publish(STREAM_OUT, {
                    "type": "persona.audio",
                    "session_id": s.id,
                    "wav_b64": base64.b64encode(wav).decode("ascii"),
                    "text": patient_text,
                })
        except Exception:
            log.exception("tts failed for session %s", s.id)

    async def _on_learner_action(self, evt: dict) -> None:
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            return
        action = evt["action"]
        ts = time.time() - s.started_at
        s.turns.append(Turn(speaker="learner", text=f"[{action}]", ts=ts, action=action))
        effect = s.physio.apply_action(action)
        await self.dhaara.publish(STREAM_OUT, {
            "type": "action.ack",
            "session_id": sid,
            "action": action,
            "effect": effect,
            "t": ts,
        })

    async def _on_physio_tick(self, evt: dict) -> None:
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            return
        s.physio.force(evt.get("perturbation", {}))

    async def _on_score_request(self, evt: dict) -> None:
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            await self.dhaara.publish(STREAM_OUT, {
                "type": "score.error",
                "session_id": sid,
                "error": "no session",
            })
            return
        trace = s.physio.trace()
        card = score_session(s.case, s.turns, trace)
        await self.dhaara.publish(STREAM_OUT, {
            "type": "score.ready",
            "session_id": sid,
            "scorecard": asdict(card),
        })

    async def _on_session_end(self, evt: dict) -> None:
        sid = evt["session_id"]
        s = self.sessions.get(sid)
        if not s:
            await self.dhaara.publish(STREAM_OUT, {"type": "session.end", "session_id": sid})
            return

        try:
            grade = s.finalize()
        except UngradableSessionError as exc:
            # A log line alone would let the learner leave believing they passed.
            log.error("summative session %s is ungradable: %s", sid, exc.reason)
            self.sessions.pop(sid, None)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "session.ungradable",
                "session_id": sid,
                "case_id": exc.case_id,
                "assessment_mode": s.assessment_mode.value,
                "reason": exc.reason,
                "requires_preceptor_review": True,
            })
            return

        await self._on_score_request(evt)
        payload: dict = {
            "type": "session.end",
            "session_id": sid,
            "assessment_mode": s.assessment_mode.value,
        }
        if grade is not None:
            payload["nirikshak_grade"] = grade.to_dict()
        if s.ledger_replay_hash is not None:
            payload["ledger_replay_hash"] = s.ledger_replay_hash
        if s.trace_migration is not None:
            payload["trace_migration"] = s.trace_migration.to_dict()
        self.sessions.pop(sid, None)
        await self.dhaara.publish(STREAM_OUT, payload)


class EventWorker:
    """Lightweight adapter: consumes gateway events and drives SessionManager (REST path)."""

    def __init__(self, manager: SessionManager, dhaara: DhaaraClient):
        self.manager = manager
        self.dhaara = dhaara
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        try:
            await self.dhaara.connect()
            self._task = asyncio.create_task(self._run())
        except Exception:
            log.warning("EventWorker: Redis not available, running REST-only mode")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        consumer = f"pratibimb-{os.getpid()}"
        async for evt in self.dhaara.consume(consumer=consumer):
            try:
                await self._handle(evt)
            except Exception as exc:
                log.error("worker_event_failed: %s, event=%s", exc, evt.get("type"))

    async def _handle(self, evt: dict) -> None:
        etype = evt.get("type")
        sid = evt.get("session_id", "")

        if etype == "case.start":
            req = CreateSessionRequest(
                learner_id=evt["learner_id"],
                target_difficulty=evt.get("target_difficulty", 0.55),
                language=Language(evt.get("language", "hi")),
            )
            resp = self.manager.create_session(req)
            pratibimb_sid = resp.session_id
            self.manager._sessions[sid] = self.manager._sessions.pop(pratibimb_sid)
            self.manager._engines[sid] = self.manager._engines.pop(pratibimb_sid)
            self.manager._emotions[sid] = self.manager._emotions.pop(pratibimb_sid)
            self.manager._histories[sid] = self.manager._histories.pop(pratibimb_sid)
            self.manager._sessions[sid].session_id = sid
            self.manager.start_session(sid)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "case.ready",
                "session_id": sid,
                "case_id": resp.case_id,
                "patient_name": resp.patient_name,
                "chief_complaint": resp.chief_complaint,
            })

        elif etype in ("dialogue", "student.utterance"):
            req = StudentUtteranceRequest(
                utterance=evt.get("utterance", evt.get("text", "")),
                language=Language(evt["language"]) if evt.get("language") else None,
            )
            result = await self.manager.handle_utterance(sid, req)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "patient.response",
                "session_id": sid,
                "patient": result.patient.model_dump(),
                "vitals": result.vitals.model_dump(),
                "elapsed_seconds": result.elapsed_seconds,
            })

        elif etype in ("clinical.action", "action"):
            req = ClinicalActionRequest(action=evt["action"], params=evt.get("params", {}))
            result = await self.manager.apply_action(sid, req)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "action.result",
                "session_id": sid,
                **result,
            })

        elif etype == "score.request":
            report = await self.manager.complete_session(sid)
            await self.dhaara.publish(STREAM_OUT, {
                "type": "score.ready",
                "session_id": sid,
                "scorecard": report.model_dump(),
            })

        elif etype == "session.end":
            await self.manager.complete_session(sid)
            await self.dhaara.publish(STREAM_OUT, {"type": "session.end", "session_id": sid})


def _chief_complaint(case: CaseBlueprint, lang_val: str) -> str:
    try:
        lang_enum = Language(lang_val)
    except ValueError:
        lang_enum = case.demographics.native_language
    return case.chief_complaint_verbatim.get(
        lang_enum,
        case.chief_complaint_verbatim.get(case.demographics.native_language, ""),
    )


def main() -> None:
    validate_ledger_startup()
    w = Worker()
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
