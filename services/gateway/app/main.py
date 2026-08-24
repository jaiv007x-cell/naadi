from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.pratibimb.app.dhaara_client import DhaaraClient, STREAM_IN
from shared.logging import setup_logging
from shared.schemas.session import (
    ClinicalActionRequest,
    CreateSessionRequest,
    StudentUtteranceRequest,
)

setup_logging()

dhaara = DhaaraClient()
SESSIONS: dict[str, asyncio.Queue] = {}


class StartCaseReq(BaseModel):
    learner_id: str
    difficulty: str = "intermediate"
    language: str = "hi"
    domain: str = "emergency_nursing"


class StartCaseResp(BaseModel):
    session_id: str
    case_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    await dhaara.connect()
    fanout_task = asyncio.create_task(_fanout())
    yield
    fanout_task.cancel()
    await dhaara.close()


app = FastAPI(title="Pratibimb Gateway", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _fanout():
    """Relay pratibimb out-stream events to WebSocket session queues."""
    async for evt in dhaara.consume_out(consumer=f"gw-{os.getpid()}"):
        sid = evt.get("session_id")
        if sid and sid in SESSIONS:
            await SESSIONS[sid].put(evt)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway"}


@app.post("/v1/cases/start", response_model=StartCaseResp)
async def start_case(req: StartCaseReq):
    sid = str(uuid.uuid4())
    SESSIONS[sid] = asyncio.Queue(maxsize=256)
    difficulty_map = {"beginner": 0.3, "intermediate": 0.55, "advanced": 0.8}
    await dhaara.publish(STREAM_IN, {
        "type": "case.start",
        "session_id": sid,
        "learner_id": req.learner_id,
        "target_difficulty": difficulty_map.get(req.difficulty, 0.55),
        "language": req.language,
        "domain": req.domain,
    })
    try:
        while True:
            evt = await asyncio.wait_for(SESSIONS[sid].get(), timeout=15)
            if evt.get("type") == "case.ready":
                return StartCaseResp(session_id=sid, case_id=evt["case_id"])
    except asyncio.TimeoutError:
        raise HTTPException(504, "case generation timed out")


@app.websocket("/v1/sessions/{session_id}/live")
async def live(ws: WebSocket, session_id: str):
    if session_id not in SESSIONS:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = SESSIONS[session_id]

    async def pump_out():
        try:
            while True:
                evt = await q.get()
                await ws.send_json(evt)
                if evt.get("type") == "session.end":
                    break
        except WebSocketDisconnect:
            pass

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive_json()
            msg["session_id"] = session_id
            await dhaara.publish(STREAM_IN, msg)
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        SESSIONS.pop(session_id, None)


@app.get("/v1/sessions/{session_id}/scorecard")
async def scorecard(session_id: str):
    if session_id not in SESSIONS:
        SESSIONS[session_id] = asyncio.Queue(maxsize=64)
    await dhaara.publish(STREAM_IN, {"type": "score.request", "session_id": session_id})
    q = SESSIONS[session_id]
    try:
        while True:
            evt = await asyncio.wait_for(q.get(), timeout=20)
            if evt.get("type") == "score.ready":
                return evt["scorecard"]
    except asyncio.TimeoutError:
        raise HTTPException(504, "scoring timed out")


# REST proxy to Pratibimb (backward-compatible MVP path)
import httpx

PRATIBIMB_URL = os.getenv("PRATIBIMB_URL", "http://localhost:8100")
DHAARA_HTTP_URL = os.getenv("DHAARA_URL", "http://localhost:8200")


async def _proxy(method: str, path: str, json: dict | None = None):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await getattr(client, method)(f"{PRATIBIMB_URL}{path}", json=json)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text)
        return resp.json()


@app.post("/v1/sessions")
async def create_session(req: CreateSessionRequest):
    return await _proxy("post", "/v1/sessions", req.model_dump())


@app.post("/v1/sessions/{session_id}/start")
async def start_session(session_id: str):
    return await _proxy("post", f"/v1/sessions/{session_id}/start")


@app.post("/v1/sessions/{session_id}/dialogue")
async def dialogue(session_id: str, req: StudentUtteranceRequest):
    return await _proxy("post", f"/v1/sessions/{session_id}/dialogue", req.model_dump())


@app.post("/v1/sessions/{session_id}/actions")
async def action(session_id: str, req: ClinicalActionRequest):
    return await _proxy("post", f"/v1/sessions/{session_id}/actions", req.model_dump())


@app.post("/v1/sessions/{session_id}/complete")
async def complete(session_id: str):
    return await _proxy("post", f"/v1/sessions/{session_id}/complete")


@app.get("/v1/competency/{learner_id}")
async def competency(learner_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DHAARA_HTTP_URL}/v1/competency/{learner_id}")
        return resp.json()
