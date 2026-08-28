from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.responses import PlainTextResponse

from shared.logging import setup_logging
from shared.schemas.scoring import SessionScoreReport
from shared.schemas.session import (
    ClinicalActionRequest,
    CreateSessionRequest,
    DialogueResponse,
    SessionResponse,
    StudentUtteranceRequest,
)

from services.pratibimb.app.config import settings
from services.pratibimb.app.dhaara_client import DhaaraClient
from services.pratibimb.app.session.manager import SessionManager
from services.pratibimb.app.worker import EventWorker
from services.pratibimb.audit.metrics import render_prometheus_metrics
from services.pratibimb.authoring.constants import PolicyRejectionCode
from services.pratibimb.authoring.errors import PublishedCaseNotFoundError
from services.pratibimb.authoring.metrics import render_authoring_prometheus_metrics
from services.pratibimb.auth.startup import validate_auth_startup
from services.pratibimb.ledger.startup import validate_ledger_startup
from services.pratibimb.ledger_read.consent_startup import validate_consent_startup
from services.pratibimb.ledger_read.auth_guard import dev_auth_refusal_response
from services.pratibimb.ledger_read.factory import get_fallback_sink_singleton
from services.pratibimb.ledger_read.summative_guard import (
    SummativeRestrictionGuard,
    summative_guard_enabled,
    summative_restriction_response,
    tenant_id_from_request_headers,
    validate_summative_startup,
)
from services.pratibimb.app.api.ledger_read_router import router as ledger_post_router
from services.pratibimb.authoring.routes import router as authoring_router
from services.pratibimb.authoring.startup import validate_authoring_startup
from services.pratibimb.ledger.corpus_reconciler import CorpusReconcilerDriver
from services.pratibimb.ledger_read.routes import router as ledger_read_router
from services.pratibimb.credentials.routes import router as credentials_router
from services.pratibimb.regrade.routes import router as regrade_router
from services.pratibimb.samvaad.dhaara_projection import (
    get_production_projection_sink,
)
from services.pratibimb.samvaad.reconciler import SamvaadProjectionReconciler
from services.pratibimb.samvaad.routes import router as samvaad_router
from services.pratibimb.arp.routes import router as arp_router

manager = SessionManager()
dhaara = DhaaraClient()
worker = EventWorker(manager, dhaara)
_corpus_reconciler: CorpusReconcilerDriver | None = None
_samvaad_reconciler: SamvaadProjectionReconciler | None = None
_summative_guard: SummativeRestrictionGuard | None = None


def get_summative_guard() -> SummativeRestrictionGuard:
    global _summative_guard
    if _summative_guard is None:
        _summative_guard = SummativeRestrictionGuard(get_fallback_sink_singleton())
    return _summative_guard


def reset_summative_guard_for_tests(guard: SummativeRestrictionGuard | None = None) -> None:
    global _summative_guard
    _summative_guard = guard


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _corpus_reconciler, _samvaad_reconciler
    setup_logging(settings.log_level)
    validate_ledger_startup()
    validate_consent_startup()
    validate_auth_startup()
    validate_summative_startup()
    validate_authoring_startup()
    _corpus_reconciler = CorpusReconcilerDriver()
    await _corpus_reconciler.start()
    _samvaad_reconciler = SamvaadProjectionReconciler(
        get_production_projection_sink()
    )
    await _samvaad_reconciler.start()
    await worker.start()
    yield
    await _samvaad_reconciler.stop()
    _samvaad_reconciler = None
    await _corpus_reconciler.stop()
    _corpus_reconciler = None
    await worker.stop()
    await dhaara.close()


app = FastAPI(
    title="Pratibimb — Clinical Twin",
    description="NAADI clinical simulation engine",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(ledger_read_router)
app.include_router(credentials_router)
app.include_router(regrade_router)
app.include_router(samvaad_router)
app.include_router(arp_router)
app.include_router(ledger_post_router)
app.include_router(authoring_router)


@app.middleware("http")
async def production_dev_auth_guard(request: Request, call_next):
    refusal = dev_auth_refusal_response(request)
    if refusal is not None:
        return refusal
    return await call_next(request)


@app.middleware("http")
async def summative_restriction_guard(request: Request, call_next):
    if summative_guard_enabled():
        refusal = summative_restriction_response(
            request,
            get_summative_guard(),
            tenant_id_provider=tenant_id_from_request_headers,
        )
        if refusal is not None:
            return refusal
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pratibimb"}


@app.get("/metrics")
async def metrics():
    body = render_prometheus_metrics() + render_authoring_prometheus_metrics()
    return PlainTextResponse(
        body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """Map published-pick contract violations to 400 INVALID_CASE_PICK."""
    for err in exc.errors():
        if err.get("type") == "invalid_case_pick":
            return JSONResponse(
                status_code=400,
                content={
                    "error": PolicyRejectionCode.INVALID_CASE_PICK.value,
                    "message": err.get("msg")
                    or "tenant_id, case_id, and case_version must be provided together",
                },
            )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.post("/v1/sessions", response_model=SessionResponse)
async def create_session(req: CreateSessionRequest):
    try:
        return manager.create_session(req)
    except PublishedCaseNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": PolicyRejectionCode.PUBLISHED_CASE_NOT_FOUND.value,
                "message": str(exc),
            },
        ) from exc


@app.post("/v1/sessions/{session_id}/start", response_model=SessionResponse)
async def start_session(session_id: str):
    try:
        return manager.start_session(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")


@app.get("/v1/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    try:
        return manager.get_session(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")


@app.post("/v1/sessions/{session_id}/dialogue", response_model=DialogueResponse)
async def dialogue(session_id: str, req: StudentUtteranceRequest):
    try:
        return await manager.handle_utterance(session_id, req)
    except KeyError:
        raise HTTPException(404, "Session not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/v1/sessions/{session_id}/actions")
async def clinical_action(session_id: str, req: ClinicalActionRequest):
    try:
        return await manager.apply_action(session_id, req)
    except KeyError:
        raise HTTPException(404, "Session not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/v1/sessions/{session_id}/complete", response_model=SessionScoreReport)
async def complete_session(session_id: str):
    try:
        return await manager.complete_session(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")


@app.get("/v1/sessions/{session_id}/events")
async def session_events(session_id: str):
    return manager.get_events(session_id)
