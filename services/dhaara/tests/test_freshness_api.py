"""Dhaara Freshness v1 API and store."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.dhaara.app.main import app
from shared.schemas.freshness import SkillFreshnessSnapshot


def _snapshot(**overrides) -> SkillFreshnessSnapshot:
    base = dict(
        learner_pseudo_id="L1",
        competency_id="DIAGNOSTIC",
        competency_before=0.70,
        competency_after=0.78,
        ability=0.78,
        confidence=0.84,
        freshness=0.63,
        evidence_age_days=71.0,
        reassessment_due=True,
        basis="policy_prior_v1",
        source_session_ids=("sess-stemi",),
        source_replay_hash="sha256:abc",
        computed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SkillFreshnessSnapshot(**base)


@pytest.fixture
def client():
    return TestClient(app)


def test_ingest_and_get_competency_state(client):
    snap = _snapshot()
    resp = client.post("/v1/freshness/ingest", json=snap.model_dump(mode="json"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ability"] == pytest.approx(0.78)
    assert body["confidence"] == pytest.approx(0.84)
    assert body["freshness"] == pytest.approx(0.63)
    assert body["reassessment_due"] is True
    assert body["basis"] == "policy_prior_v1"

    get_resp = client.get("/v1/freshness/L1/DIAGNOSTIC")
    assert get_resp.status_code == 200
    assert get_resp.json()["last_session_id"] == "sess-stemi"


def test_list_states_and_timeline(client):
    snap = _snapshot()
    client.post("/v1/freshness/ingest", json=snap.model_dump(mode="json"))

    list_resp = client.get("/v1/freshness/L1")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["states"]) == 1

    timeline = client.get("/v1/timeline/L1/DIAGNOSTIC")
    assert timeline.status_code == 200
    entries = timeline.json()["entries"]
    assert len(entries) >= 1
    assert entries[0]["session_id"] == "sess-stemi"


def test_get_missing_state_returns_404(client):
    resp = client.get("/v1/freshness/L-missing/DIAGNOSTIC")
    assert resp.status_code == 404
