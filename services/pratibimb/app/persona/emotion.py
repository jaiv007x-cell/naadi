from __future__ import annotations

import re
from dataclasses import dataclass, replace

from shared.schemas.case import CaseBlueprint

_EMPATHY = re.compile(
    r"\b(समझ|चिंत|आराम|ठीक होगा|don't worry|i understand|it's okay|breathe|namaste|kasa|kaise|sorry|madad|aaram)\b",
    re.I,
)
_HARSH = re.compile(r"\b(shut up|calm down|चुप|बकवास|stop crying)\b", re.I)


@dataclass(frozen=True)
class EmotionState:
    """
    Immutable emotion snapshot — valence/arousal/pain/trust model.
    Worker path uses update_emotion(); REST path uses update_from_student() which
    returns a new EmotionState.
    """

    valence: float = -0.2  # -1..1
    arousal: float = 0.5  # 0..1
    pain: float = 5.0  # 0..10
    trust: float = 0.4  # 0..1

    # Backward-compat aliases used by prompts.py and session manager
    @property
    def anxiety(self) -> float:
        return self.arousal * 10.0

    @property
    def comprehension(self) -> float:
        return max(0.0, min(1.0, 0.5 + self.trust * 0.5 - self.arousal * 0.3))

    @classmethod
    def from_case(cls, case: CaseBlueprint) -> EmotionState:
        initial_pain = float(getattr(case, "initial_pain", 0) or _guess_pain(case))
        return cls(valence=-0.2, arousal=0.5, pain=initial_pain, trust=0.4)

    @classmethod
    def default(cls, pain: float = 7.0, anxiety: float = 6.0, trust: float = 5.0) -> EmotionState:
        return cls(
            valence=-0.1,
            arousal=min(1.0, anxiety / 10.0),
            pain=pain,
            trust=min(1.0, trust / 10.0),
        )

    def update_from_student(
        self, empathy_score: float, jargon_level: float, pain_delta: float = 0.0
    ) -> "EmotionState":
        v = self.valence + 0.10 * empathy_score - 0.15 * jargon_level
        a = self.arousal - 0.05 * empathy_score + 0.08 * jargon_level
        p = self.pain + pain_delta
        tr = self.trust + 0.08 * empathy_score - 0.12 * jargon_level
        return replace(
            self,
            valence=max(-1.0, min(1.0, v)),
            arousal=max(0.0, min(1.0, a)),
            pain=max(0.0, min(10.0, p)),
            trust=max(0.0, min(1.0, tr)),
        )


def update_emotion(
    prev: EmotionState, learner_text: str, physio: dict | object
) -> EmotionState:
    """Full update from learner text + physio snapshot (worker path)."""
    v, a, p, tr = prev.valence, prev.arousal, prev.pain, prev.trust

    if _EMPATHY.search(learner_text):
        v += 0.10
        tr += 0.08
        a -= 0.05
    if _HARSH.search(learner_text):
        v -= 0.20
        tr -= 0.15
        a += 0.10

    snap = physio if isinstance(physio, dict) else (physio.as_dict() if hasattr(physio, "as_dict") else {})
    hr = snap.get("hr", 90)
    if hr > 120:
        a = min(1.0, a + 0.05)
    if snap.get("spo2", 98) < 92:
        a = min(1.0, a + 0.08)
        v -= 0.05

    p = max(0.0, min(10.0, p + (0.2 if a > 0.7 else -0.1)))

    return replace(
        prev,
        valence=max(-1.0, min(1.0, v)),
        arousal=max(0.0, min(1.0, a)),
        pain=p,
        trust=max(0.0, min(1.0, tr)),
    )


def _guess_pain(case: CaseBlueprint) -> float:
    """Heuristic initial pain from diagnosis."""
    dx = case.hidden.primary_diagnosis.lower()
    if "stemi" in dx or "mi" in dx or "infarction" in dx:
        return 7.0
    if "fracture" in dx:
        return 8.0
    if "sepsis" in dx:
        return 5.0
    return 4.0


# Legacy compat — old code that did `clamp()` directly
def clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))
