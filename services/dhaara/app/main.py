from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.dhaara.app.freshness_store import FreshnessStore
from shared.logging import setup_logging
from shared.schemas.freshness import CompetencyState, SkillFreshnessSnapshot
from shared.schemas.timeline import TimelineEntry, project_snapshot_to_timeline

setup_logging()

# Legacy in-memory competency graph stub (score deltas from Pratibimb)
_competency_graph: dict[str, dict[str, float]] = {}

# Dhaara Freshness v1 — derived exclusively from SkillFreshnessSnapshot
_freshness_store = FreshnessStore()


class CompetencyUpdateRequest(BaseModel):
    learner_id: str
    session_id: str
    case_id: str
    overall_score: float
    updates: list[dict] = Field(default_factory=list)


class FreshnessIngestBatch(BaseModel):
    snapshots: tuple[SkillFreshnessSnapshot, ...] = ()


app = FastAPI(title="Dhaara — Competency Graph", version="0.2.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dhaara", "freshness_v1": True}


@app.post("/v1/competency/update")
async def update_competency(req: CompetencyUpdateRequest):
    graph = _competency_graph.setdefault(req.learner_id, {})
    for u in req.updates:
        tag = u.get("competency_tag", "")
        current = graph.get(tag, 0.5)
        graph[tag] = round(min(1.0, max(0.0, current + u.get("delta", 0.0))), 4)

    return {
        "status": "updated",
        "learner_id": req.learner_id,
        "session_id": req.session_id,
        "graph": graph,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.get("/v1/competency/{learner_id}")
async def get_competency(learner_id: str):
    return {
        "learner_id": learner_id,
        "graph": _competency_graph.get(learner_id, {}),
    }


@app.post("/v1/freshness/ingest")
async def ingest_freshness(snapshot: SkillFreshnessSnapshot) -> CompetencyState:
    """Accept a single SkillFreshnessSnapshot; update competency state projection."""
    return _freshness_store.ingest(snapshot)


@app.post("/v1/freshness/ingest/batch")
async def ingest_freshness_batch(batch: FreshnessIngestBatch) -> dict:
    states = _freshness_store.ingest_many(batch.snapshots)
    return {"status": "ingested", "count": len(states)}


@app.get("/v1/freshness/{learner_pseudo_id}")
async def list_freshness_states(learner_pseudo_id: str) -> dict:
    states = _freshness_store.list_states(learner_pseudo_id)
    return {
        "learner_pseudo_id": learner_pseudo_id,
        "states": [s.model_dump(mode="json") for s in states],
    }


@app.get("/v1/freshness/{learner_pseudo_id}/{competency_id}")
async def get_freshness_state(learner_pseudo_id: str, competency_id: str) -> CompetencyState:
    state = _freshness_store.get_state(learner_pseudo_id, competency_id)
    if state is None:
        raise HTTPException(status_code=404, detail="competency state not found")
    return state


@app.get("/v1/timeline/{learner_pseudo_id}/{competency_id}")
async def get_competency_timeline(learner_pseudo_id: str, competency_id: str) -> dict:
    """
    Evidence timeline — read-only projection over ingested freshness snapshots.
    Every row links to underlying session provenance.
    """
    entries: list[TimelineEntry] = []
    for snap in _freshness_store.snapshots:
        if snap.learner_pseudo_id != learner_pseudo_id or snap.competency_id != competency_id:
            continue
        entries.extend(project_snapshot_to_timeline(snap))
    entries.sort(key=lambda e: e.at)
    return {
        "learner_pseudo_id": learner_pseudo_id,
        "competency_id": competency_id,
        "entries": [e.model_dump(mode="json") for e in entries],
    }
