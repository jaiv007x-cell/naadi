"""Tests for Skill Decay Model v1."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from services.pratibimb.app.beema.skill_decay import (
    CompetencyRecord,
    CompetencySpec,
    DecaySignal,
    Observation,
    SkillClass,
    TAU_DAYS,
    compute_decay_signal,
    scan_clinician,
)


def _spec(
    skill_class: SkillClass = SkillClass.COGNITIVE_ALGORITHMIC,
    recert_threshold: float = 0.60,
) -> CompetencySpec:
    return CompetencySpec(
        competency_id="acls_resus",
        display_name="ACLS Resuscitation",
        skill_class=skill_class,
        recert_threshold=recert_threshold,
    )


def _obs(
    days_ago: float = 0.0,
    theta: float = 0.90,
    sigma: float = 0.05,
    now: datetime | None = None,
) -> Observation:
    base = now or datetime(2026, 8, 1)
    return Observation(
        t=base - timedelta(days=days_ago),
        theta=theta,
        sigma=sigma,
        source="simulation",
    )


# ── Decay model ──────────────────────────────────────────────────────────────

def test_theta_at_observation_time():
    spec = _spec()
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90)])
    t = datetime(2026, 8, 1)
    assert rec.theta_at(t) == pytest.approx(0.90, abs=0.001)


def test_theta_decays_toward_baseline():
    spec = _spec()
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90)])
    t = datetime(2026, 8, 1) + timedelta(days=spec.tau_days)
    theta = rec.theta_at(t)
    expected = spec.theta_baseline + (0.90 - spec.theta_baseline) * math.exp(-1.0)
    assert theta == pytest.approx(expected, abs=0.001)


def test_theta_baseline_floor():
    spec = _spec()
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90)])
    far_future = datetime(2026, 8, 1) + timedelta(days=3650)
    theta = rec.theta_at(far_future)
    assert theta >= spec.theta_baseline
    assert theta < spec.theta_baseline + 0.01


def test_no_observations_gives_baseline():
    spec = _spec()
    rec = CompetencyRecord("C001", spec)
    assert rec.theta_at(datetime(2026, 8, 1)) == spec.theta_baseline


def test_sigma_grows_with_time():
    spec = _spec()
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, sigma=0.05)])
    t0 = datetime(2026, 8, 1)
    s0 = rec.sigma_at(t0)
    s30 = rec.sigma_at(t0 + timedelta(days=30))
    s90 = rec.sigma_at(t0 + timedelta(days=90))
    assert s0 < s30 < s90


# ── Recertification triggers ─────────────────────────────────────────────────

def test_needs_recert_when_theta_below_threshold():
    spec = _spec(recert_threshold=0.60)
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.55, now=now)])
    assert rec.needs_recertification(now) is True


def test_no_recert_when_fresh():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90, now=now)])
    assert rec.needs_recertification(now) is False


def test_recert_triggered_by_uncertainty():
    """Even if point estimate is OK, high uncertainty triggers recert."""
    spec = _spec(recert_threshold=0.60)
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.65, sigma=0.15, now=now)])
    # theta - 2*sigma = 0.65 - 0.30 = 0.35 < hard_floor (0.40)
    assert rec.needs_recertification(now) is True


def test_days_until_recert_positive():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90, now=now)])
    days = rec.days_until_recert(now)
    assert days is not None
    assert days > 0


def test_days_until_recert_zero_when_past():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.55, now=now)])
    assert rec.days_until_recert(now) == 0.0


# ── DecaySignal ───────────────────────────────────────────────────────────────

def test_decay_signal_needs_recert():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.55, now=now)])
    sig = compute_decay_signal(rec, now)
    assert sig.needs_recert is True
    assert sig.recommended_action == "schedule_recertification"
    assert sig.clinician_id == "C001"


def test_decay_signal_no_action():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.95, now=now)])
    sig = compute_decay_signal(rec, now)
    assert sig.needs_recert is False
    assert sig.recommended_action == "no_action"


def test_decay_signal_to_dict():
    spec = _spec()
    now = datetime(2026, 8, 1)
    rec = CompetencyRecord("C001", spec, [_obs(days_ago=0, theta=0.90, now=now)])
    d = compute_decay_signal(rec, now).to_dict()
    assert "current_theta" in d
    assert "recommended_action" in d
    assert isinstance(d["current_theta"], float)


def test_scan_clinician_multiple_competencies():
    now = datetime(2026, 8, 1)
    records = [
        CompetencyRecord("C001", _spec(), [_obs(days_ago=0, theta=0.90, now=now)]),
        CompetencyRecord(
            "C001",
            CompetencySpec("airway_mgmt", "Airway Management",
                           SkillClass.PSYCHOMOTOR_LOW_FREQ),
            [_obs(days_ago=200, theta=0.85, now=now)],
        ),
    ]
    signals = scan_clinician(records, now)
    assert len(signals) == 2
    decayed = [s for s in signals if s.needs_recert]
    assert len(decayed) >= 1  # airway should have decayed after 200 days


# ── Skill class tau values ────────────────────────────────────────────────────

def test_tau_ordering():
    """Psychomotor low-freq skills decay fastest."""
    assert TAU_DAYS[SkillClass.PSYCHOMOTOR_LOW_FREQ] < TAU_DAYS[SkillClass.PSYCHOMOTOR_HIGH_FREQ]
    assert TAU_DAYS[SkillClass.COGNITIVE_ALGORITHMIC] < TAU_DAYS[SkillClass.COGNITIVE_JUDGMENT]
