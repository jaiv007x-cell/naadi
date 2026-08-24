"""
Skill Decay Model v1.

Converts recertification from calendar-based to evidence-based.

Per (clinician_id, competency_id), maintains a posterior over ability
theta in [0, 1]. Between observations, the posterior decays toward a
competency-specific baseline at a rate calibrated to the skill class.

v1: modified Ebbinghaus decay with literature-derived tau values.
v2: empirically-fit tau from Virohan longitudinal re-assessment data.

BEEMA contract: this module is READ-ONLY over the Evidence Ledger.
It produces DecaySignal objects; it never writes judgments upstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Sequence


class SkillClass(str, Enum):
    PSYCHOMOTOR_HIGH_FREQ = "psychomotor_high_freq"
    PSYCHOMOTOR_LOW_FREQ = "psychomotor_low_freq"
    COGNITIVE_ALGORITHMIC = "cognitive_algorithmic"
    COGNITIVE_JUDGMENT = "cognitive_judgment"
    COMMUNICATION = "communication"


TAU_DAYS: dict[SkillClass, float] = {
    SkillClass.PSYCHOMOTOR_HIGH_FREQ: 365.0,
    SkillClass.PSYCHOMOTOR_LOW_FREQ: 120.0,
    SkillClass.COGNITIVE_ALGORITHMIC: 180.0,
    SkillClass.COGNITIVE_JUDGMENT: 240.0,
    SkillClass.COMMUNICATION: 300.0,
}


@dataclass(frozen=True)
class CompetencySpec:
    competency_id: str
    display_name: str
    skill_class: SkillClass
    recert_threshold: float = 0.60
    hard_floor: float = 0.40
    theta_baseline: float = 0.20
    sigma_growth_rate: float = 0.02

    @property
    def tau_days(self) -> float:
        return TAU_DAYS[self.skill_class]

    @property
    def tau_seconds(self) -> float:
        return self.tau_days * 86400.0


@dataclass
class Observation:
    """A single competency measurement."""
    t: datetime
    theta: float
    sigma: float
    source: str  # "simulation", "preceptor", "micro_assessment"
    session_id: str = ""


@dataclass
class CompetencyRecord:
    """Longitudinal record for one (clinician, competency) pair."""
    clinician_id: str
    spec: CompetencySpec
    observations: list[Observation] = field(default_factory=list)

    @property
    def last_observation(self) -> Observation | None:
        return self.observations[-1] if self.observations else None

    def add_observation(self, obs: Observation) -> None:
        self.observations.append(obs)
        self.observations.sort(key=lambda o: o.t)

    def theta_at(self, t: datetime) -> float:
        """Decayed ability estimate at time t."""
        last = self.last_observation
        if last is None:
            return self.spec.theta_baseline

        dt_s = max(0.0, (t - last.t).total_seconds())
        return self.spec.theta_baseline + (
            (last.theta - self.spec.theta_baseline)
            * math.exp(-dt_s / self.spec.tau_seconds)
        )

    def sigma_at(self, t: datetime) -> float:
        """Uncertainty at time t. Grows with sqrt(days since last obs)."""
        last = self.last_observation
        if last is None:
            return 0.30  # prior uncertainty

        dt_days = max(0.0, (t - last.t).total_seconds() / 86400.0)
        return last.sigma + self.spec.sigma_growth_rate * math.sqrt(dt_days)

    def needs_recertification(self, t: datetime) -> bool:
        theta = self.theta_at(t)
        sigma = self.sigma_at(t)
        return (
            theta < self.spec.recert_threshold
            or (theta - 2 * sigma) < self.spec.hard_floor
        )

    def days_until_recert(self, t: datetime) -> float | None:
        """
        Estimate days from t until recert threshold is crossed.
        Returns None if already past threshold or no observations.
        """
        if self.needs_recertification(t):
            return 0.0

        last = self.last_observation
        if last is None:
            return None

        delta = last.theta - self.spec.theta_baseline
        if delta <= 0:
            return 0.0

        target_theta = self.spec.recert_threshold
        ratio = (target_theta - self.spec.theta_baseline) / delta
        if ratio <= 0 or ratio >= 1:
            return None

        dt_s = -self.spec.tau_seconds * math.log(ratio)
        already_elapsed = (t - last.t).total_seconds()
        remaining_s = dt_s - already_elapsed
        return max(0.0, remaining_s / 86400.0)


@dataclass(frozen=True)
class DecaySignal:
    """Emitted by BEEMA when a competency needs attention."""
    clinician_id: str
    competency_id: str
    competency_name: str
    current_theta: float
    current_sigma: float
    recert_threshold: float
    days_since_last_obs: float
    days_until_recert: float | None
    needs_recert: bool
    recommended_action: str

    def to_dict(self) -> dict:
        return {
            "clinician_id": self.clinician_id,
            "competency_id": self.competency_id,
            "competency_name": self.competency_name,
            "current_theta": round(self.current_theta, 4),
            "current_sigma": round(self.current_sigma, 4),
            "recert_threshold": self.recert_threshold,
            "days_since_last_obs": round(self.days_since_last_obs, 1),
            "days_until_recert": round(self.days_until_recert, 1) if self.days_until_recert is not None else None,
            "needs_recert": self.needs_recert,
            "recommended_action": self.recommended_action,
        }


def compute_decay_signal(
    record: CompetencyRecord,
    now: datetime,
) -> DecaySignal:
    theta = record.theta_at(now)
    sigma = record.sigma_at(now)
    needs = record.needs_recertification(now)
    days_until = record.days_until_recert(now)

    last = record.last_observation
    days_since = (now - last.t).total_seconds() / 86400.0 if last else float("inf")

    if needs:
        action = "schedule_recertification"
    elif days_until is not None and days_until < 30:
        action = "schedule_micro_assessment"
    elif days_since > record.spec.tau_days * 0.5:
        action = "monitor_closely"
    else:
        action = "no_action"

    return DecaySignal(
        clinician_id=record.clinician_id,
        competency_id=record.spec.competency_id,
        competency_name=record.spec.display_name,
        current_theta=theta,
        current_sigma=sigma,
        recert_threshold=record.spec.recert_threshold,
        days_since_last_obs=days_since,
        days_until_recert=days_until,
        needs_recert=needs,
        recommended_action=action,
    )


def scan_clinician(
    records: Sequence[CompetencyRecord],
    now: datetime,
) -> list[DecaySignal]:
    """Produce decay signals for all competencies of a clinician."""
    return [compute_decay_signal(r, now) for r in records]
