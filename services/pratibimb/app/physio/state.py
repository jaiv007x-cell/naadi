"""
PhysiologyEngine — deterministic physiology simulation with compartmental PK/PD.

Three layers per tick:
1. Disease baseline — deterioration if critical actions are missing
2. Pharmacodynamic overlay — via PharmacologyEngine (compartmental, Hill Emax)
3. Crisis detection + death check

PharmacologyEngine owns all drug state; PhysiologyEngine translates PD deltas
to clamped vital updates and manages the clinical-level concerns (allergy,
contraindication, reversal routing).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from shared.schemas.case import CaseBlueprint, Vital
from shared.schemas.trace import LegacyTraceMigration
from shared.schemas.trace import PhysioTrace as TypedTrace
from shared.schemas.flag_causes import FlagCause, FlagClearReason
from .pharmacology import (
    DRUG_REGISTRY,
    DoseEvent,
    PharmacologyEngine,
    Route,
    check_contraindicated,
    get_drug,
)


class Clock:
    """
    Injectable time source.

    Production: delegates to `time.time()`. Probe/test: `SimClock` advances
    only when told, making the engine fully deterministic.
    """

    def now(self) -> float:
        return time.time()


class SimClock(Clock):
    """Deterministic clock for probes and tests. Starts at zero."""

    def __init__(self, t: float = 0.0) -> None:
        self._t = t

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> float:
        self._t += dt
        return self._t

    def set(self, t: float) -> None:
        self._t = t


@dataclass
class PhysioState:
    vitals: Vital
    baseline_vitals: Vital
    started_at: float = field(default_factory=time.time)
    interventions: list[dict] = field(default_factory=list)
    reperfused: bool = False
    dead: bool = False
    snapshots: list[dict] = field(default_factory=list)
    crisis_started: float | None = None


@dataclass
class PhysioSnapshot:
    t: float
    hr: float
    sbp: float
    spo2: float
    dead: bool = False


@dataclass
class PhysioTrace:
    snapshots: list[PhysioSnapshot] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    flag_history: list[dict] = field(default_factory=list)
    active_flags: dict[str, float] = field(default_factory=dict)

    def record(self, t: float, vitals: Vital, dead: bool = False) -> None:
        self.snapshots.append(
            PhysioSnapshot(t=t, hr=vitals.hr, sbp=vitals.sbp, spo2=vitals.spo2, dead=dead)
        )

    def add_event(self, t: float, kind: str, data: dict) -> None:
        self.events.append({"t": t, "kind": kind, **data})

    def set_flag(
        self,
        flag: str,
        t: float,
        cause: FlagCause,
        *,
        ttl_s: float | None = None,
        detail: dict | None = None,
    ) -> None:
        if not isinstance(cause, FlagCause):
            raise TypeError(
                f"set_flag(cause=...) requires FlagCause enum, got {type(cause).__name__}"
            )
        already_active = flag in self.active_flags
        self.active_flags[flag] = t + (ttl_s or 9999)
        entry = {
            "flag": flag,
            "set": True,
            "t": t,
            "cause": cause.value,
            "namespace": FlagCause.namespace(cause),
            "detail": detail or {},
        }
        self.flag_history.append(entry)
        if not already_active:
            self.add_event(t, "flag_raised", {
                "flag": flag,
                "ttl_s": ttl_s,
                "cause": cause.value,
                "namespace": FlagCause.namespace(cause),
                "detail": detail or {},
            })

    def clear_flag(
        self,
        flag: str,
        t: float,
        cause: FlagCause = FlagCause.PHYSIOLOGY_HOMEOSTASIS,
        *,
        detail: dict | None = None,
    ) -> None:
        if not isinstance(cause, FlagCause):
            raise TypeError(
                f"clear_flag(cause=...) requires FlagCause enum, got {type(cause).__name__}"
            )
        if flag not in self.active_flags:
            return
        self.active_flags.pop(flag, None)
        entry = {
            "flag": flag,
            "set": False,
            "t": t,
            "cause": cause.value,
            "namespace": FlagCause.namespace(cause),
            "detail": detail or {},
            "reason": _clear_cause_token(cause),
        }
        self.flag_history.append(entry)
        kind = "flag_expired" if cause is FlagCause.PHYSIOLOGY_TIMEOUT else "flag_cleared"
        self.add_event(t, kind, {
            "flag": flag,
            "cause": cause.value,
            "namespace": FlagCause.namespace(cause),
            "detail": detail or {},
            "reason": _clear_cause_token(cause),
        })

    def first_crisis_window(self) -> tuple[float, float] | None:
        if len(self.snapshots) < 2:
            return None
        baseline_sbp = self.snapshots[0].sbp
        for i, snap in enumerate(self.snapshots):
            if snap.dead or snap.sbp < baseline_sbp - 15 or snap.spo2 < 92:
                t0 = snap.t
                t1 = snap.t
                for later in self.snapshots[i:]:
                    if later.sbp < baseline_sbp - 10 or later.spo2 < 93:
                        t1 = later.t
                return (t0, t1)
        return None

    def drug_admin_times(self) -> dict[str, float]:
        """Map of drug_name -> first admin time (seconds from start)."""
        out: dict[str, float] = {}
        for ev in self.events:
            if ev.get("kind") in ("medication", "dose") and ev.get("drug") not in out:
                t = ev.get("t", ev.get("t_min", 0) * 60)
                out[ev["drug"]] = t
        return out

    def serialize(self) -> dict:
        return {
            "snapshots": [{"t": s.t, "hr": s.hr, "sbp": s.sbp, "spo2": s.spo2, "dead": s.dead} for s in self.snapshots],
            "events": list(self.events),
            "flag_history": list(self.flag_history),
            "active_flags": dict(self.active_flags),
            "drug_admins": self.drug_admin_times(),
        }

    def to_canonical(self) -> dict:
        """Deterministic dict for hashing. Floats rounded to 6dp."""
        def _r(v: float) -> float:
            return round(v, 6)

        return {
            "snapshots": [
                {"dead": s.dead, "hr": _r(s.hr), "sbp": _r(s.sbp), "spo2": _r(s.spo2), "t": _r(s.t)}
                for s in self.snapshots
            ],
            "events": sorted(
                [
                    {k: (_r(v) if isinstance(v, float) else v) for k, v in ev.items() if k != "id"}
                    for ev in self.events
                ],
                key=lambda e: e.get("t", e.get("t_min", 0)),
            ),
            "flag_history": [
                {"flag": fh["flag"], "set": fh["set"], "t": _r(fh["t"])}
                for fh in self.flag_history
            ],
        }

    def to_typed_trace(
        self, case_id: str = "", case_version: str = "0.0.0", *, strict: bool = False
    ) -> LegacyTraceMigration:
        """
        Project this dict-event trace onto the typed grader contract in
        `shared.schemas.trace`. Nirikshak only ever reads the typed form.

        Returns the trace bundled with its migration audit; unpack as
        `trace, report = legacy.to_typed_trace()`.
        """
        return TypedTrace.from_legacy(
            self, case_id=case_id, case_version=case_version, strict=strict
        )

    @classmethod
    def from_engine(cls, engine: PhysiologyEngine) -> PhysioTrace:
        trace = cls()
        if engine.state.snapshots:
            for s in engine.state.snapshots:
                trace.snapshots.append(PhysioSnapshot(**{
                    k: s[k] for k in ("t", "hr", "sbp", "spo2", "dead") if k in s
                }))
        else:
            trace.record(0.0, engine.state.vitals, engine.state.dead)
        trace.events = list(engine.pharm.events)
        trace.flag_history = list(engine._flag_history)
        trace.active_flags = dict(engine.pharm.active_flags)
        return trace


_LEGACY_ROUTE_MAP = {
    "IV_PUSH": Route.IV_BOLUS,
    "IV_INFUSION": Route.IV_INFUSION,
    "iv_bolus": Route.IV_BOLUS,
    "iv_infusion": Route.IV_INFUSION,
    "IM": Route.IM, "im": Route.IM,
    "PO": Route.PO, "po": Route.PO,
    "SL": Route.SL, "sl": Route.SL,
    "INH": Route.INHALED, "inhaled": Route.INHALED,
    "SC": Route.SC, "sc": Route.SC,
    "PR": Route.PR, "pr": Route.PR,
}


def _clear_cause_token(cause: FlagCause) -> str:
    if cause is FlagCause.PHYSIOLOGY_TIMEOUT:
        return "ttl_expired"
    if cause is FlagCause.PHYSIOLOGY_HOMEOSTASIS:
        return "flag_expired_or_resolved"
    if cause is FlagCause.PHYSIOLOGY_COMPENSATION:
        return "spontaneous_resolution"
    return cause.value.split(".")[-1]


def _flag_entry(
    flag: str,
    *,
    is_set: bool,
    t: float,
    cause: FlagCause,
    detail: dict | None = None,
) -> dict:
    entry = {
        "flag": flag,
        "set": is_set,
        "t": t,
        "cause": cause.value,
        "namespace": FlagCause.namespace(cause),
        "detail": detail or {},
    }
    if not is_set:
        entry["reason"] = _clear_cause_token(cause)
    return entry


class PhysiologyEngine:
    """
    Top-level simulation engine. Owns PhysioState + PharmacologyEngine.
    """

    def __init__(
        self,
        case: CaseBlueprint,
        weight_kg: float = 70.0,
        clock: Clock | None = None,
    ):
        self.case = case
        self.weight_kg = weight_kg
        self._clock = clock or Clock()
        self.state = PhysioState(
            vitals=case.baseline_vitals.model_copy(),
            baseline_vitals=case.baseline_vitals.model_copy(),
            started_at=self._clock.now(),
        )
        self.pharm = PharmacologyEngine(DRUG_REGISTRY)
        self._hepatic_function = 1.0
        self._renal_function = 1.0
        FlagCause.validate_registered()
        self._flag_history: list[dict] = []
        self._prev_flags: set[str] = set()
        self._pending_flag_causes: dict[str, tuple[FlagCause, dict]] = {}

    @property
    def flag_history(self) -> list[dict]:
        return self._flag_history

    def set_flag(
        self,
        flag: str,
        cause: FlagCause,
        *,
        ttl_s: float | None = None,
        detail: dict | None = None,
    ) -> None:
        if not isinstance(cause, FlagCause):
            raise TypeError(
                f"set_flag(cause=...) requires FlagCause enum, got {type(cause).__name__}"
            )
        elapsed = self._elapsed()
        if ttl_s is not None:
            self.pharm.active_flags[flag] = self._elapsed_min() + ttl_s / 60.0
        else:
            self.pharm.active_flags[flag] = self._elapsed_min() + 9999.0
        self._flag_history.append(
            _flag_entry(flag, is_set=True, t=elapsed, cause=cause, detail=detail)
        )
        self._prev_flags = set(self.pharm.active_flags.keys())

    def clear_flag(
        self,
        flag: str,
        cause: FlagCause = FlagCause.PHYSIOLOGY_HOMEOSTASIS,
        *,
        detail: dict | None = None,
    ) -> None:
        if not isinstance(cause, FlagCause):
            raise TypeError(
                f"clear_flag(cause=...) requires FlagCause enum, got {type(cause).__name__}"
            )
        if flag not in self.pharm.active_flags:
            return
        self.pharm.active_flags.pop(flag, None)
        self._flag_history.append(
            _flag_entry(
                flag,
                is_set=False,
                t=self._elapsed(),
                cause=cause,
                detail=detail,
            )
        )
        self._prev_flags = set(self.pharm.active_flags.keys())

    def _elapsed(self) -> float:
        return self._clock.now() - self.state.started_at

    def _elapsed_min(self) -> float:
        return self._elapsed() / 60.0

    def _patient_context(self) -> dict:
        return {
            "weight_kg": self.weight_kg,
            "hepatic_function": self._hepatic_function,
            "renal_function": self._renal_function,
        }

    # ── Medication administration ─────────────────────────────────────────

    def administer_medication(
        self,
        drug_name: str,
        dose: Optional[float] = None,
        route: Optional[str] = None,
        weight_kg: Optional[float] = None,
        duration_min: Optional[float] = None,
    ) -> dict:
        spec = get_drug(drug_name)
        if not spec:
            return {"observed": f"Unknown drug '{drug_name}'", "error": True}

        wt = weight_kg or self.weight_kg
        if dose is None:
            dose = spec.default_dose_mg * (wt if spec.weight_scaled else 1.0)

        vitals_dict = {"hr": self.state.vitals.hr, "sbp": self.state.vitals.sbp, "spo2": self.state.vitals.spo2}
        elapsed = self._elapsed()
        elapsed_min = elapsed / 60.0

        # Allergy check
        allergies = getattr(self.case.hidden, "allergies", []) or []
        if drug_name.lower() in [a.lower() for a in allergies]:
            self.pharm.events.append({"t": elapsed, "t_min": elapsed_min, "kind": "allergy", "drug": drug_name})
            self.pharm.active_flags["allergic_reaction"] = self.pharm.t_min + 10.0
            self._pending_flag_causes["allergic_reaction"] = (
                FlagCause.DRUG_ALLERGY,
                {"drug": spec.name, "dose_mg": dose, "route": route or spec.default_route.value},
            )
            for f in spec.adverse_flags:
                self.pharm.active_flags[f] = self.pharm.t_min + 10.0
                self._pending_flag_causes[f] = (
                    FlagCause.CLINICAL_ANAPHYLAXIS_ONSET,
                    {"trigger_drug": spec.name},
                )
            self.state.interventions.append({"t": elapsed, "action": f"give_{drug_name}", "params": {"dose": dose, "allergic": True}})
            return {"observed": f"ALLERGIC REACTION to {drug_name}", "adverse": True,
                    "flags": list(spec.adverse_flags | frozenset({"allergic_reaction"}))}

        # Contraindication check
        if check_contraindicated(drug_name.lower(), vitals_dict):
            self.pharm.events.append({"t": elapsed, "t_min": elapsed_min, "kind": "contraindicated", "drug": drug_name})
            for f in spec.adverse_flags:
                self.pharm.active_flags[f] = self.pharm.t_min + 10.0
                self._pending_flag_causes[f] = (
                    FlagCause.DRUG_CONTRAINDICATED,
                    {"drug": spec.name},
                )
            self.state.interventions.append({"t": elapsed, "action": f"give_{drug_name}", "params": {"dose": dose, "contraindicated": True}})
            return {"observed": f"CONTRAINDICATION: {drug_name} given despite unsafe vitals", "adverse": True,
                    "flags": list(spec.adverse_flags)}

        # Reversal agents
        if spec.reverses:
            reversed_names = self.pharm.reverse(drug_name, spec.reverses)
            if reversed_names:
                for rn in reversed_names:
                    target_spec = get_drug(rn)
                    if target_spec:
                        for f in (target_spec.adverse_flags | frozenset(pf for pd in target_spec.pd for pf in pd.flags_above_frac)):
                            self.pharm.active_flags.pop(f, None)

        # Resolve route
        resolved_route = spec.default_route
        if route:
            resolved_route = _LEGACY_ROUTE_MAP.get(route, spec.default_route)

        # Create dose event
        dose_evt = DoseEvent(
            drug=spec.name,
            route=resolved_route,
            amount_mg=dose if duration_min is None else dose,  # for infusion: amount_mg = rate mg/min
            t_start_min=elapsed_min,
            duration_min=duration_min,
        )

        try:
            self.pharm.administer(dose_evt)
        except KeyError:
            return {"observed": f"Drug '{drug_name}' not in registry", "error": True}

        # Interaction check (flag only — PK clearance modifiers handle the pharmacology)
        interaction_with = None
        for other_name in spec.interactions:
            if other_name in self.pharm.states:
                st = self.pharm.states[other_name]
                if st.a_central_mg > 0.01 or st.ce_mg_per_l > 0.001:
                    interaction_with = other_name
                    break

        self.state.interventions.append({"t": elapsed, "action": f"give_{spec.name}", "params": {"dose": dose}})

        result: dict = {
            "observed": f"{spec.name} {dose} {spec.dose_unit} given via {resolved_route.value}",
        }
        if interaction_with:
            result["interaction_warning"] = f"Co-administered with {interaction_with} — clearance modified"
        if duration_min:
            result["infusion_duration_min"] = duration_min

        return result

    # ── Legacy apply_action (backward compat for REST path) ───────────────

    def apply_action(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        now = self._elapsed()

        med_map = {
            "give_morphine": ("morphine", None),
            "give_oxygen": (None, None),
            "give_streptokinase": ("streptokinase", None),
            "give_aspirin_325_chewed": ("aspirin", 325.0),
            "give_atorvastatin_80": (None, None),
        }
        if action in med_map:
            drug_name, dose = med_map[action]
            if drug_name:
                return self.administer_medication(drug_name, dose=dose)
            self.state.interventions.append({"t": now, "action": action, "params": params})
            if action == "give_oxygen":
                self.state.vitals.spo2 = min(100, self.state.vitals.spo2 + 2)
                return {"observed": "SpO2 improves"}
            if action == "give_atorvastatin_80":
                return {"observed": "atorvastatin 80mg given", "onset_hr": 24}
            if action == "give_aspirin_325_chewed":
                return {"observed": "patient chews aspirin", "onset_min": 20}

        self.state.interventions.append({"t": now, "action": action, "params": params})

        if action in ("order_ecg_within_10min", "order_ecg"):
            return {
                "observed": "12-lead acquired",
                "findings": "ST elevation 2mm in II, III, aVF; reciprocal changes in I, aVL",
                "turnaround_s": 45,
            }
        if action == "order_troponin":
            return {"observed": "sample sent", "turnaround_min": 40}
        if action == "arrange_pci_transfer":
            return {"observed": "ambulance dispatched, PCI center notified", "eta_min": 25}
        if action == "measure_vitals":
            return {"observed": "vitals recorded", "vitals": self.state.vitals.model_dump()}

        return {"observed": f"action '{action}' logged", "unknown": True}

    # ── Tick ──────────────────────────────────────────────────────────────

    def tick(self, seconds: float) -> Vital:
        elapsed = self._elapsed()
        v = self.state.vitals

        # 1. Disease baseline deterioration
        critical_taken = {i["action"] for i in self.state.interventions}
        missing_critical = set(self.case.critical_actions) - critical_taken
        if elapsed > 600 and missing_critical and not self.state.reperfused:
            v.hr += 0.3 * (seconds / 60)
            v.sbp -= 0.5 * (seconds / 60)
            v.spo2 = max(88, v.spo2 - 0.1 * (seconds / 60))

        # 2. Pharmacodynamic overlay via compartmental PK/PD engine
        dt_min = seconds / 60.0
        effects = self.pharm.step(dt_min, self._patient_context())

        # Apply PD deltas (rate-scaled)
        v.hr = max(20, min(250, v.hr + effects.get("hr", 0.0) * dt_min))
        v.sbp = max(50, min(250, v.sbp + effects.get("sbp", 0.0) * dt_min))
        v.dbp = max(30, min(150, v.dbp + effects.get("dbp", 0.0) * dt_min))
        v.rr = max(6, min(40, v.rr + effects.get("rr", 0.0) * dt_min))
        v.spo2 = max(70, min(100, v.spo2 + effects.get("spo2", 0.0) * dt_min))
        v.temp_c = max(34, min(42, v.temp_c + effects.get("temp_c", 0.0) * dt_min))

        # 3. Expire flags + crisis detection
        active_flags = self.pharm.active_flags
        crisis = (
            v.hr > 130 or v.sbp < 80 or v.spo2 < 88
            or "respiratory_arrest" in active_flags
            or "cardiogenic_shock" in active_flags
        )
        if crisis and self.state.crisis_started is None:
            self.state.crisis_started = elapsed
        elif not crisis:
            self.state.crisis_started = None

        # 4. Death
        if v.sbp < 60 or v.spo2 < 75 or v.hr > 200 or v.hr < 30:
            self.state.dead = True

        # 5. Record flag transitions with cause/reason from vocabulary
        current_flags = set(active_flags.keys())
        for f in current_flags - self._prev_flags:
            pending = (
                self._pending_flag_causes.pop(f, None)
                or self.pharm.pending_flag_causes.pop(f, None)
            )
            if pending is not None:
                cause, detail = pending
            else:
                cause, detail = FlagCause.PHYSIOLOGY_PHARMACODYNAMIC, {}
            self._flag_history.append(
                _flag_entry(f, is_set=True, t=elapsed, cause=cause, detail=detail)
            )
        for f in self._prev_flags - current_flags:
            self._flag_history.append(
                _flag_entry(
                    f,
                    is_set=False,
                    t=elapsed,
                    cause=FlagCause.PHYSIOLOGY_HOMEOSTASIS,
                )
            )
        self._prev_flags = current_flags

        self._record_snapshot(elapsed)
        return v

    def tick_to(self, target_s: float, step_s: float = 1.0) -> None:
        """
        Advance the simulation to `target_s` (seconds from session start) using
        fixed `step_s` ticks. Requires a `SimClock` — raises if the engine was
        constructed with a wall clock.

        This is the probe entry point. Every call to `tick()` under the hood
        sees the same elapsed-time sequence regardless of wall-clock speed, so
        the resulting trace is deterministic.
        """
        if not isinstance(self._clock, SimClock):
            raise TypeError("tick_to requires a SimClock for deterministic replay")
        while self._elapsed() < target_s and not self.state.dead:
            dt = min(step_s, target_s - self._elapsed())
            self._clock.advance(dt)
            self.tick(dt)

    # ── Utilities ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        v = self.state.vitals
        return {
            "hr": v.hr, "sbp": v.sbp, "dbp": v.dbp,
            "spo2": v.spo2, "rr": v.rr, "temp_c": v.temp_c,
            "dead": self.state.dead,
            "flags": list(self.pharm.active_flags.keys()),
            "drugs": self.pharm.snapshot(),
        }

    def force(self, perturbation: dict) -> None:
        v = self.state.vitals
        for key in ("hr", "sbp", "dbp", "spo2", "rr", "temp_c"):
            if key in perturbation:
                setattr(v, key, perturbation[key])
        if "dead" in perturbation:
            self.state.dead = perturbation["dead"]

    def trace(self) -> PhysioTrace:
        return PhysioTrace.from_engine(self)

    def _record_snapshot(self, elapsed: float) -> None:
        v = self.state.vitals
        self.state.snapshots.append({
            "t": elapsed, "hr": v.hr, "sbp": v.sbp, "spo2": v.spo2, "dead": self.state.dead,
        })
