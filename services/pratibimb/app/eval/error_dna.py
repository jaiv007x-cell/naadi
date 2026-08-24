"""
Error-DNA: six-dimensional performance pattern vector per session.

Internally `error_dna`; externally "Performance Pattern Vector."
All dimensions are floats in [0.0, 1.0] where 1.0 = ideal.

The six dimensions capture orthogonal failure modes that a scalar grade
cannot separate:

  1. recognition_delay   — time from first crisis signal to learner acknowledgment
  2. action_sequencing   — fraction of critical actions taken in correct order
  3. dose_accuracy       — 1 - mean relative dose error across administered drugs
  4. escalation_timing   — time from crisis onset to first escalation
  5. recovery_velocity   — speed of crisis flag resolution after intervention
  6. communication_score — placeholder for LLM-judge; 0.5 default until wired

Each dimension is computed from typed-trace events only. No duck-typing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from shared.schemas.trace import PhysioTrace, FlagEvent


@dataclass(frozen=True)
class ErrorDNA:
    recognition_delay: float
    action_sequencing: float
    dose_accuracy: float
    escalation_timing: float
    recovery_velocity: float
    communication_score: float

    def to_dict(self) -> dict:
        return asdict(self)

    def as_vector(self) -> tuple[float, ...]:
        return (
            self.recognition_delay,
            self.action_sequencing,
            self.dose_accuracy,
            self.escalation_timing,
            self.recovery_velocity,
            self.communication_score,
        )

    @property
    def mean(self) -> float:
        v = self.as_vector()
        return sum(v) / len(v)


# ── dimension computers ──────────────────────────────────────────────────────

_CRISIS_FLAGS = frozenset({
    "cardiogenic_shock", "respiratory_arrest", "anaphylaxis",
    "hypotensive_crisis", "bradycardia_critical", "tachycardia_critical",
})


def _recognition_delay(
    trace: PhysioTrace,
    time_pressure_s: float,
) -> float:
    """
    How quickly the learner acknowledged the first crisis signal.
    1.0 = recognized within 60s of first crisis flag; 0.0 = never or at session end.
    """
    first_crisis_t: float | None = None
    for flag in _CRISIS_FLAGS:
        t = trace.flag_set_at(flag)
        if t is not None and (first_crisis_t is None or t < first_crisis_t):
            first_crisis_t = t

    if first_crisis_t is None:
        return 1.0  # no crisis → nothing to recognize

    recog_events = trace.recognitions()
    if not recog_events:
        return 0.0

    first_recog_t = min(r.t_s for r in recog_events)
    delay = max(0.0, first_recog_t - first_crisis_t)
    window = min(time_pressure_s, 300.0)
    return max(0.0, 1.0 - delay / window)


def _action_sequencing(
    trace: PhysioTrace,
    expected_sequence: Sequence[str],
) -> float:
    """
    Fraction of expected critical actions taken in the correct relative order.
    Uses the longest ordered subsequence over action timestamps.
    """
    if len(expected_sequence) <= 1:
        return 1.0

    actual_times: dict[str, float] = {}
    for d in trace.drug_admins():
        key = d.drug_id.lower()
        if key not in actual_times:
            actual_times[key] = d.t_s
    for o in trace.orders():
        key = o.order_id.lower()
        if key not in actual_times:
            actual_times[key] = o.t_s
    for e in trace.escalations():
        key = e.target.lower()
        if key not in actual_times:
            actual_times[key] = e.t_s

    seq_times = [actual_times.get(a.lower()) for a in expected_sequence]
    present = [(i, t) for i, t in enumerate(seq_times) if t is not None]

    if len(present) <= 1:
        return 1.0 if present else 0.0

    # longest increasing subsequence length / total present
    times_only = [t for _, t in present]
    lis = _lis_length(times_only)
    return lis / len(present)


def _lis_length(arr: list[float]) -> int:
    """Length of longest increasing subsequence — O(n log n)."""
    import bisect
    tails: list[float] = []
    for v in arr:
        pos = bisect.bisect_left(tails, v)
        if pos == len(tails):
            tails.append(v)
        else:
            tails[pos] = v
    return len(tails)


def _dose_accuracy(
    trace: PhysioTrace,
    expected_doses: dict[str, float] | None,
) -> float:
    """
    1.0 - mean |actual - expected| / expected across administered drugs.
    If no expected doses provided, returns 0.5 (unknown).
    """
    if not expected_doses:
        return 0.5

    errors: list[float] = []
    for drug_id, expected in expected_doses.items():
        admins = trace.drug_admins(drug_id)
        if admins:
            actual = admins[0].dose
            rel_err = abs(actual - expected) / max(expected, 1e-6)
            errors.append(min(rel_err, 1.0))

    if not errors:
        return 0.5
    return max(0.0, 1.0 - sum(errors) / len(errors))


def _escalation_timing(
    trace: PhysioTrace,
    time_pressure_s: float,
) -> float:
    """
    How fast the learner escalated after crisis onset.
    1.0 = escalated within 60s; 0.0 = never escalated during crisis.
    """
    first_crisis_t: float | None = None
    for flag in _CRISIS_FLAGS:
        t = trace.flag_set_at(flag)
        if t is not None and (first_crisis_t is None or t < first_crisis_t):
            first_crisis_t = t

    if first_crisis_t is None:
        return 1.0

    escalations = trace.escalations()
    if not escalations:
        return 0.0

    first_esc_t = min(e.t_s for e in escalations)
    delay = max(0.0, first_esc_t - first_crisis_t)
    window = min(time_pressure_s, 300.0)
    return max(0.0, 1.0 - delay / window)


def _recovery_velocity(trace: PhysioTrace) -> float:
    """
    Average speed of crisis resolution. Uses flag_spans on crisis flags.
    1.0 = all crisis flags resolved within 120s; 0.0 = never resolved or >600s avg.
    """
    durations: list[float] = []
    for flag in _CRISIS_FLAGS:
        spans = trace.flag_spans(flag)
        for lo, hi in spans:
            dur = hi - lo
            durations.append(dur)

    if not durations:
        return 1.0  # no crisis → perfect

    avg_dur = sum(durations) / len(durations)
    # map 0s → 1.0, 600s → 0.0
    return max(0.0, min(1.0, 1.0 - avg_dur / 600.0))


# ── public API ────────────────────────────────────────────────────────────────

def compute_error_dna(
    trace: PhysioTrace,
    *,
    expected_sequence: Sequence[str] = (),
    expected_doses: dict[str, float] | None = None,
    time_pressure_s: float = 900.0,
    communication_score: float = 0.5,
) -> ErrorDNA:
    return ErrorDNA(
        recognition_delay=_recognition_delay(trace, time_pressure_s),
        action_sequencing=_action_sequencing(trace, expected_sequence),
        dose_accuracy=_dose_accuracy(trace, expected_doses),
        escalation_timing=_escalation_timing(trace, time_pressure_s),
        recovery_velocity=_recovery_velocity(trace),
        communication_score=communication_score,
    )
