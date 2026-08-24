"""
Compartmental PK/PD pharmacology engine.

Architecture:
- 1-compartment or 2-compartment PK per drug (RK2 integrator)
- Hill Emax PD with effect-site equilibration (ke0)
- Tolerance dynamics (first-order ec50 shift)
- CYP substrate clearance modifiers + organ function scaling
- Infusion vs bolus distinction (DoseEvent.duration_min)
- Drug interactions via clearance multipliers on co-administered drugs
- Contraindication predicates separate from Drug specs
- Reversal agents cancel target drug depot + central amounts
- All state is plain-data serialisable (DrugState is a dataclass, no callables)

Integration contract with PhysiologyEngine:
  PharmacologyEngine.step(dt_min, patient_context) -> dict[str, float]
  Returns additive deltas keyed by target vital (hr, sbp, dbp, rr, spo2, temp_c, glucose).
  PhysiologyEngine applies these deltas each tick.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from math import exp
from typing import Optional

from shared.schemas.flag_cause import FlagCause


# ── Enums ────────────────────────────────────────────────────────────────────

class Route(str, Enum):
    IV_BOLUS = "iv_bolus"
    IV_INFUSION = "iv_infusion"
    PO = "po"
    IM = "im"
    SC = "sc"
    INHALED = "inhaled"
    SL = "sl"
    PR = "pr"

class Compartment(str, Enum):
    ONE = "1c"
    TWO = "2c"


# ── PK / PD parameter structs ───────────────────────────────────────────────

@dataclass(frozen=True)
class PKParams:
    vd_central_l_per_kg: float
    vd_peripheral_l_per_kg: float = 0.0
    cl_l_per_min_per_kg: float = 0.0
    q_l_per_min_per_kg: float = 0.0
    ka_per_min: float = 0.0
    bioavailability: float = 1.0
    protein_binding: float = 0.0
    hepatic_fraction: float = 0.5
    renal_fraction: float = 0.5
    cyp_substrates: tuple[str, ...] = ()
    model: Compartment = Compartment.ONE

@dataclass(frozen=True)
class PDParams:
    emax: float
    ec50_mg_per_l: float
    hill: float = 1.0
    baseline: float = 0.0
    target: str = "sbp"
    ke0_per_min: float = 0.0
    tolerance_tau_min: float = 0.0
    tolerance_max_shift: float = 0.0
    flags_above_frac: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DrugSpec:
    name: str
    class_name: str
    pk: PKParams
    pd: tuple[PDParams, ...]
    default_dose_mg: float = 0.0
    dose_unit: str = "mg"
    weight_scaled: bool = False
    default_route: Route = Route.IV_BOLUS
    interactions: dict[str, float] = field(default_factory=dict)
    reverses: tuple[str, ...] = ()
    toxic_plasma_mg_per_l: float = 0.0
    adverse_flags: frozenset[str] = frozenset()


# ── Mutable per-drug state (fully serialisable) ─────────────────────────────

@dataclass
class DrugState:
    a_central_mg: float = 0.0
    a_peripheral_mg: float = 0.0
    a_depot_mg: float = 0.0
    ce_mg_per_l: float = 0.0
    tolerance_shift: float = 0.0
    cumulative_dose_mg: float = 0.0

    def as_dict(self) -> dict:
        return {
            "a_central_mg": self.a_central_mg,
            "a_peripheral_mg": self.a_peripheral_mg,
            "a_depot_mg": self.a_depot_mg,
            "ce_mg_per_l": self.ce_mg_per_l,
            "tolerance_shift": self.tolerance_shift,
            "cumulative_dose_mg": self.cumulative_dose_mg,
        }


@dataclass
class DoseEvent:
    drug: str
    route: Route
    amount_mg: float
    t_start_min: float
    duration_min: Optional[float] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def as_dict(self) -> dict:
        return {
            "drug": self.drug, "route": self.route.value,
            "amount_mg": self.amount_mg, "t_start_min": self.t_start_min,
            "duration_min": self.duration_min, "id": self.id,
        }


# ── Contraindication predicates ──────────────────────────────────────────────

def _sbp_below(t: float):
    return lambda v: v.get("sbp", 120) < t
def _hr_below(t: float):
    return lambda v: v.get("hr", 70) < t
def _hr_above(t: float):
    return lambda v: v.get("hr", 70) > t
def _spo2_below(t: float):
    return lambda v: v.get("spo2", 98) < t

CONTRAINDICATIONS: dict[str, list] = {
    "morphine": [_sbp_below(90), _spo2_below(90)],
    "fentanyl": [_sbp_below(90)],
    "tramadol": [_sbp_below(100)],
    "midazolam": [_sbp_below(90)],
    "amiodarone": [_sbp_below(90)],
    "metoprolol": [_hr_below(60), _sbp_below(90)],
    "nitroglycerin_sl": [_sbp_below(90)],
    "atropine": [_hr_above(120)],
}

def check_contraindicated(drug_name: str, vitals: dict) -> bool:
    return any(c(vitals) for c in CONTRAINDICATIONS.get(drug_name, []))


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ── PharmacologyEngine ───────────────────────────────────────────────────────

class PharmacologyEngine:
    """
    Multi-drug PK/PD engine. Time unit: minutes.

    Patient context dict expected keys:
      weight_kg, hepatic_function (0-1), renal_function (0-1)
    """

    def __init__(self, registry: dict[str, DrugSpec] | None = None):
        self.drugs: dict[str, DrugSpec] = dict(registry or DRUG_REGISTRY)
        self.states: dict[str, DrugState] = {}
        self.doses: list[DoseEvent] = []
        self.events: list[dict] = []
        self.active_flags: dict[str, float] = {}
        self.pending_flag_causes: dict[str, tuple[FlagCause, dict]] = {}
        self.t_min: float = 0.0

    def administer(self, dose: DoseEvent) -> str:
        if dose.drug not in self.drugs:
            raise KeyError(f"unknown drug: {dose.drug}")
        self.doses.append(dose)
        self.states.setdefault(dose.drug, DrugState())
        self.states[dose.drug].cumulative_dose_mg += dose.amount_mg * (dose.duration_min or 1.0) if dose.duration_min else dose.amount_mg
        self.events.append({
            "t_min": dose.t_start_min, "kind": "dose",
            "drug": dose.drug, "amount_mg": dose.amount_mg,
            "route": dose.route.value, "id": dose.id,
        })
        return dose.id

    def step(self, dt_min: float, patient: dict) -> dict[str, float]:
        """Advance PK/PD by dt_min. Returns aggregated vital deltas."""
        self.t_min += dt_min
        weight = max(1.0, patient.get("weight_kg", 70.0))
        hep = _clamp(patient.get("hepatic_function", 1.0), 0.05, 1.5)
        ren = _clamp(patient.get("renal_function", 1.0), 0.05, 1.5)

        self._deposit_doses(dt_min, weight)

        for name, spec in self.drugs.items():
            st = self.states.get(name)
            if st is None:
                continue
            self._integrate_pk(name, spec, st, dt_min, weight, hep, ren)

        effects: dict[str, float] = {}
        for name, spec in self.drugs.items():
            st = self.states.get(name)
            if st is None:
                continue
            cp = self._plasma_conc(spec, st, weight)
            self._update_effect_site(spec, st, cp, dt_min)

            # Toxicity flag check
            if spec.toxic_plasma_mg_per_l > 0 and cp >= spec.toxic_plasma_mg_per_l:
                for flag in spec.adverse_flags:
                    if flag not in self.active_flags:
                        self.pending_flag_causes[flag] = (
                            FlagCause.DRUG_TOXIC,
                            {"drug": name, "cp": cp},
                        )
                    self.active_flags[flag] = self.t_min + 10.0
                if cp >= spec.toxic_plasma_mg_per_l:
                    self.events.append({"t_min": self.t_min, "kind": "toxic_level", "drug": name, "cp": cp})

            for pd in spec.pd:
                e = self._hill_effect(pd, st, dt_min)
                effects[pd.target] = effects.get(pd.target, 0.0) + e
                # PD-level flags
                if pd.flags_above_frac:
                    frac = self._effect_fraction(pd, st)
                    for flag, threshold in pd.flags_above_frac.items():
                        if frac >= threshold:
                            if flag not in self.active_flags:
                                self.pending_flag_causes[flag] = (
                                    FlagCause.DRUG_PD_EFFECT,
                                    {"drug": name, "target": pd.target},
                                )
                            self.active_flags[flag] = self.t_min + 5.0

        # Expire flags
        expired = [f for f, exp in self.active_flags.items() if self.t_min > exp]
        for f in expired:
            self.active_flags.pop(f)

        return effects

    def reverse(self, antidote: str, targets: tuple[str, ...]) -> list[str]:
        """Zero out central + depot for target drugs. Returns list of reversed names."""
        reversed_names = []
        for t in targets:
            st = self.states.get(t)
            if st and (st.a_central_mg > 0.01 or st.a_depot_mg > 0.01):
                st.a_central_mg = 0.0
                st.a_peripheral_mg = 0.0
                st.a_depot_mg = 0.0
                st.ce_mg_per_l = 0.0
                reversed_names.append(t)
                self.doses = [d for d in self.doses if d.drug != t]
        if reversed_names:
            self.events.append({"t_min": self.t_min, "kind": "reversal", "antidote": antidote, "reversed": reversed_names})
        return reversed_names

    def plasma_conc(self, drug_name: str, weight_kg: float = 70.0) -> float:
        spec = self.drugs.get(drug_name)
        st = self.states.get(drug_name)
        if not spec or not st:
            return 0.0
        return self._plasma_conc(spec, st, weight_kg)

    def snapshot(self) -> dict:
        return {
            name: st.as_dict()
            for name, st in self.states.items()
            if st.a_central_mg > 0.001 or st.a_depot_mg > 0.001 or st.ce_mg_per_l > 0.001
        }

    # ── Internal PK integration ──────────────────────────────────────────

    def _deposit_doses(self, dt_min: float, weight: float) -> None:
        t0, t1 = self.t_min - dt_min, self.t_min
        remaining: list[DoseEvent] = []
        for d in self.doses:
            spec = self.drugs[d.drug]
            st = self.states[d.drug]

            if d.duration_min is None or d.duration_min == 0:
                # Bolus / single-shot
                if t0 <= d.t_start_min < t1:
                    if d.route in (Route.IV_BOLUS, Route.IV_INFUSION):
                        st.a_central_mg += d.amount_mg
                    else:
                        st.a_depot_mg += d.amount_mg * spec.pk.bioavailability
                elif d.t_start_min >= t1:
                    remaining.append(d)
                continue

            # Infusion: rate = amount_mg (mg/min), deliver over overlap
            end = d.t_start_min + d.duration_min
            overlap = max(0.0, min(t1, end) - max(t0, d.t_start_min))
            if overlap > 0:
                delivered = d.amount_mg * overlap
                if d.route in (Route.IV_BOLUS, Route.IV_INFUSION):
                    st.a_central_mg += delivered
                else:
                    st.a_depot_mg += delivered * spec.pk.bioavailability
            if end > t1:
                remaining.append(d)

        self.doses = remaining

    def _integrate_pk(self, name: str, spec: DrugSpec, st: DrugState,
                      dt: float, weight: float, hep: float, ren: float) -> None:
        pk = spec.pk
        vd_c = pk.vd_central_l_per_kg * weight
        vd_p = pk.vd_peripheral_l_per_kg * weight
        cl = pk.cl_l_per_min_per_kg * weight * (pk.hepatic_fraction * hep + pk.renal_fraction * ren)

        # Interaction modifiers on clearance
        for other, mult in spec.interactions.items():
            if other in self.states and self._plasma_conc(self.drugs.get(other, spec), self.states[other], weight) > 0.001:
                cl *= mult

        q = pk.q_l_per_min_per_kg * weight
        ka = pk.ka_per_min

        def derivs(ac: float, ap: float, ad: float):
            cp = ac / vd_c if vd_c > 0 else 0.0
            cper = ap / vd_p if vd_p > 0 else 0.0
            dac = ka * ad - cl * cp
            if pk.model == Compartment.TWO and vd_p > 0:
                dac += q * (cper - cp)
                dap = q * (cp - cper)
            else:
                dap = 0.0
            dad = -ka * ad
            return dac, dap, dad

        # RK2 midpoint method
        k1 = derivs(st.a_central_mg, st.a_peripheral_mg, st.a_depot_mg)
        mid = (
            st.a_central_mg + 0.5 * dt * k1[0],
            st.a_peripheral_mg + 0.5 * dt * k1[1],
            st.a_depot_mg + 0.5 * dt * k1[2],
        )
        k2 = derivs(*mid)
        st.a_central_mg = max(0.0, st.a_central_mg + dt * k2[0])
        st.a_peripheral_mg = max(0.0, st.a_peripheral_mg + dt * k2[1])
        st.a_depot_mg = max(0.0, st.a_depot_mg + dt * k2[2])

    def _plasma_conc(self, spec: DrugSpec, st: DrugState, weight: float) -> float:
        vd_c = spec.pk.vd_central_l_per_kg * weight
        if vd_c <= 0:
            return 0.0
        cp_total = st.a_central_mg / vd_c
        return cp_total * (1.0 - spec.pk.protein_binding)

    def _update_effect_site(self, spec: DrugSpec, st: DrugState, cp: float, dt: float) -> None:
        ke0 = max((pd.ke0_per_min for pd in spec.pd), default=0.0)
        if ke0 <= 0:
            st.ce_mg_per_l = cp
        else:
            st.ce_mg_per_l += ke0 * (cp - st.ce_mg_per_l) * dt

    def _effect_fraction(self, pd: PDParams, st: DrugState) -> float:
        ec50_eff = pd.ec50_mg_per_l + st.tolerance_shift
        if ec50_eff <= 0:
            return 1.0 if st.ce_mg_per_l > 0 else 0.0
        ce = st.ce_mg_per_l
        num = ce ** pd.hill
        den = ec50_eff ** pd.hill + num
        return num / den if den > 0 else 0.0

    def _hill_effect(self, pd: PDParams, st: DrugState, dt: float) -> float:
        frac = self._effect_fraction(pd, st)
        effect = pd.baseline + pd.emax * frac

        if pd.tolerance_tau_min > 0 and pd.tolerance_max_shift != 0:
            target_shift = pd.tolerance_max_shift * frac
            st.tolerance_shift += (target_shift - st.tolerance_shift) * (dt / pd.tolerance_tau_min)

        return effect


# ── Drug Registry ────────────────────────────────────────────────────────────
# PK params sourced from standard references (simplified for sim fidelity).
# ka_per_min: absorption rate for non-IV routes.
# For IV bolus, ka=0 (direct central injection).

def _iv_drug(name: str, class_name: str, vd: float, cl: float,
             pd: tuple[PDParams, ...], default_dose: float = 0.0,
             dose_unit: str = "mg", weight_scaled: bool = False,
             interactions: dict | None = None, reverses: tuple[str, ...] = (),
             toxic_cp: float = 0.0, adverse_flags: frozenset[str] = frozenset(),
             hep_frac: float = 0.5, ren_frac: float = 0.5,
             protein_binding: float = 0.0,
             ) -> DrugSpec:
    return DrugSpec(
        name=name, class_name=class_name,
        pk=PKParams(
            vd_central_l_per_kg=vd, cl_l_per_min_per_kg=cl,
            hepatic_fraction=hep_frac, renal_fraction=ren_frac,
            protein_binding=protein_binding,
        ),
        pd=pd, default_dose_mg=default_dose, dose_unit=dose_unit,
        weight_scaled=weight_scaled, default_route=Route.IV_BOLUS,
        interactions=interactions or {}, reverses=reverses,
        toxic_plasma_mg_per_l=toxic_cp, adverse_flags=adverse_flags,
    )

def _oral_drug(name: str, class_name: str, vd: float, cl: float, ka: float, bio: float,
               pd: tuple[PDParams, ...], default_dose: float = 0.0,
               dose_unit: str = "mg", interactions: dict | None = None,
               toxic_cp: float = 0.0, adverse_flags: frozenset[str] = frozenset(),
               hep_frac: float = 0.7, ren_frac: float = 0.3,
               protein_binding: float = 0.0,
               ) -> DrugSpec:
    return DrugSpec(
        name=name, class_name=class_name,
        pk=PKParams(
            vd_central_l_per_kg=vd, cl_l_per_min_per_kg=cl,
            ka_per_min=ka, bioavailability=bio,
            hepatic_fraction=hep_frac, renal_fraction=ren_frac,
            protein_binding=protein_binding,
        ),
        pd=pd, default_dose_mg=default_dose, dose_unit=dose_unit,
        default_route=Route.PO,
        interactions=interactions or {},
        toxic_plasma_mg_per_l=toxic_cp, adverse_flags=adverse_flags,
    )

def _2c_drug(name: str, class_name: str, vd_c: float, vd_p: float, cl: float, q: float,
             pd: tuple[PDParams, ...], default_dose: float = 0.0,
             dose_unit: str = "mg", weight_scaled: bool = False,
             route: Route = Route.IV_BOLUS,
             ka: float = 0.0, bio: float = 1.0,
             interactions: dict | None = None, reverses: tuple[str, ...] = (),
             toxic_cp: float = 0.0, adverse_flags: frozenset[str] = frozenset(),
             hep_frac: float = 0.5, ren_frac: float = 0.5,
             protein_binding: float = 0.0,
             ) -> DrugSpec:
    return DrugSpec(
        name=name, class_name=class_name,
        pk=PKParams(
            vd_central_l_per_kg=vd_c, vd_peripheral_l_per_kg=vd_p,
            cl_l_per_min_per_kg=cl, q_l_per_min_per_kg=q,
            ka_per_min=ka, bioavailability=bio,
            hepatic_fraction=hep_frac, renal_fraction=ren_frac,
            protein_binding=protein_binding,
            model=Compartment.TWO,
        ),
        pd=pd, default_dose_mg=default_dose, dose_unit=dose_unit,
        weight_scaled=weight_scaled, default_route=route,
        interactions=interactions or {}, reverses=reverses,
        toxic_plasma_mg_per_l=toxic_cp, adverse_flags=adverse_flags,
    )


DRUG_REGISTRY: dict[str, DrugSpec] = {
    # ── Opioid analgesics ──
    "morphine": _2c_drug(
        "morphine", "opioid analgesic",
        vd_c=0.15, vd_p=0.20, cl=0.015, q=0.005,
        default_dose=4.0, dose_unit="mg",
        hep_frac=0.7, ren_frac=0.3, protein_binding=0.35,
        pd=(
            PDParams(emax=-10.0, ec50_mg_per_l=0.03, hill=1.5, target="sbp", ke0_per_min=0.02,
                     flags_above_frac={"analgesia": 0.3}),
            PDParams(emax=-4.0, ec50_mg_per_l=0.03, hill=1.5, target="rr", ke0_per_min=0.02,
                     tolerance_tau_min=120.0, tolerance_max_shift=0.02,
                     flags_above_frac={"respiratory_depression": 0.7}),
            PDParams(emax=-3.0, ec50_mg_per_l=0.03, hill=1.0, target="hr", ke0_per_min=0.02),
        ),
        interactions={"midazolam": 0.5, "diazepam": 0.5},
        toxic_cp=0.1, adverse_flags=frozenset({"respiratory_depression", "respiratory_arrest"}),
    ),
    "fentanyl": _2c_drug(
        "fentanyl", "opioid analgesic",
        vd_c=0.10, vd_p=0.50, cl=0.013, q=0.008,
        default_dose=0.05, dose_unit="mg",
        hep_frac=0.9, ren_frac=0.1, protein_binding=0.85,
        pd=(
            PDParams(emax=-8.0, ec50_mg_per_l=0.001, hill=2.0, target="sbp", ke0_per_min=0.10,
                     flags_above_frac={"analgesia": 0.3}),
            PDParams(emax=-6.0, ec50_mg_per_l=0.001, hill=2.0, target="rr", ke0_per_min=0.10,
                     flags_above_frac={"respiratory_depression": 0.6}),
            PDParams(emax=-5.0, ec50_mg_per_l=0.001, hill=1.5, target="hr", ke0_per_min=0.10),
        ),
        interactions={"midazolam": 0.4, "diazepam": 0.4},
        toxic_cp=0.003, adverse_flags=frozenset({"respiratory_depression", "respiratory_arrest"}),
    ),
    "tramadol": _iv_drug(
        "tramadol", "weak opioid",
        vd=0.30, cl=0.008,
        default_dose=50.0,
        pd=(
            PDParams(emax=-5.0, ec50_mg_per_l=0.3, hill=1.0, target="sbp",
                     flags_above_frac={"analgesia": 0.3}),
            PDParams(emax=-2.0, ec50_mg_per_l=0.3, hill=1.0, target="rr"),
        ),
        interactions={"morphine": 0.6, "fentanyl": 0.6},
        toxic_cp=1.0, adverse_flags=frozenset({"respiratory_depression"}),
    ),

    # ── Reversal agents ──
    "naloxone": _iv_drug(
        "naloxone", "opioid antagonist",
        vd=0.20, cl=0.025, default_dose=0.4,
        pd=(), reverses=("morphine", "fentanyl", "tramadol"),
    ),
    "flumazenil": _iv_drug(
        "flumazenil", "benzo antagonist",
        vd=0.10, cl=0.015, default_dose=0.2,
        pd=(), reverses=("midazolam", "diazepam"),
    ),

    # ── Sedatives ──
    "midazolam": _2c_drug(
        "midazolam", "benzodiazepine",
        vd_c=0.10, vd_p=0.15, cl=0.008, q=0.003,
        default_dose=2.0, protein_binding=0.96,
        hep_frac=0.95, ren_frac=0.05,
        pd=(
            PDParams(emax=-6.0, ec50_mg_per_l=0.05, hill=1.5, target="sbp",
                     flags_above_frac={"sedation": 0.3}),
            PDParams(emax=-2.0, ec50_mg_per_l=0.05, hill=1.5, target="rr",
                     flags_above_frac={"respiratory_depression": 0.7}),
        ),
        interactions={"morphine": 0.4, "fentanyl": 0.4},
        toxic_cp=0.15, adverse_flags=frozenset({"sedation_overshoot", "respiratory_arrest"}),
    ),
    "ketamine": _iv_drug(
        "ketamine", "dissociative",
        vd=0.30, cl=0.020, default_dose=1.0, dose_unit="mg/kg",
        weight_scaled=True, protein_binding=0.12,
        pd=(
            PDParams(emax=+12.0, ec50_mg_per_l=1.0, hill=1.5, target="hr",
                     flags_above_frac={"dissociation": 0.3}),
            PDParams(emax=+12.0, ec50_mg_per_l=1.0, hill=1.5, target="sbp"),
        ),
        toxic_cp=5.0, adverse_flags=frozenset({"hypertensive_crisis", "emergence_reaction"}),
    ),

    # ── Cardiac ──
    "adrenaline": _iv_drug(
        "adrenaline", "catecholamine",
        vd=0.15, cl=0.050, default_dose=1.0,
        pd=(
            PDParams(emax=+35.0, ec50_mg_per_l=0.01, hill=1.0, target="sbp"),
            PDParams(emax=+25.0, ec50_mg_per_l=0.01, hill=1.0, target="hr"),
        ),
        interactions={"metoprolol": 1.5},
        toxic_cp=0.05, adverse_flags=frozenset({"tachyarrhythmia", "vf_risk"}),
    ),
    "atropine": _iv_drug(
        "atropine", "anticholinergic",
        vd=0.20, cl=0.010, default_dose=0.5,
        pd=(
            PDParams(emax=+18.0, ec50_mg_per_l=0.02, hill=1.0, target="hr"),
        ),
        toxic_cp=0.08, adverse_flags=frozenset({"tachyarrhythmia"}),
    ),
    "amiodarone": _2c_drug(
        "amiodarone", "antiarrhythmic III",
        vd_c=0.10, vd_p=1.0, cl=0.003, q=0.005,
        default_dose=150.0, route=Route.IV_INFUSION,
        protein_binding=0.96, hep_frac=0.9, ren_frac=0.1,
        pd=(
            PDParams(emax=-12.0, ec50_mg_per_l=1.0, hill=1.0, target="hr", ke0_per_min=0.005,
                     flags_above_frac={"rhythm_stabilizing": 0.3}),
            PDParams(emax=-8.0, ec50_mg_per_l=1.0, hill=1.0, target="sbp", ke0_per_min=0.005),
        ),
        interactions={"metoprolol": 0.5},
        toxic_cp=3.0, adverse_flags=frozenset({"hypotension", "bradycardia"}),
    ),
    "metoprolol": _iv_drug(
        "metoprolol", "beta-blocker",
        vd=0.25, cl=0.015, default_dose=5.0, protein_binding=0.12,
        pd=(
            PDParams(emax=-15.0, ec50_mg_per_l=0.05, hill=1.0, target="hr"),
            PDParams(emax=-10.0, ec50_mg_per_l=0.05, hill=1.0, target="sbp"),
        ),
        interactions={"verapamil": 0.3, "diltiazem": 0.3},
        toxic_cp=0.2, adverse_flags=frozenset({"bradycardia", "cardiogenic_shock"}),
    ),
    "nitroglycerin_sl": DrugSpec(
        name="nitroglycerin_sl", class_name="nitrate",
        pk=PKParams(vd_central_l_per_kg=0.20, cl_l_per_min_per_kg=0.050,
                    ka_per_min=0.20, bioavailability=0.40,
                    hepatic_fraction=1.0, renal_fraction=0.0),
        pd=(
            PDParams(emax=-18.0, ec50_mg_per_l=0.002, hill=1.0, target="sbp",
                     flags_above_frac={"preload_reduction": 0.3}),
            PDParams(emax=-8.0, ec50_mg_per_l=0.002, hill=1.0, target="dbp"),
            PDParams(emax=+5.0, ec50_mg_per_l=0.002, hill=1.0, target="hr"),
        ),
        default_dose_mg=0.4, default_route=Route.SL,
        toxic_plasma_mg_per_l=0.01, adverse_flags=frozenset({"severe_hypotension"}),
    ),

    # ── Antiplatelet / anticoagulant (flag-only PD — no vital deltas) ──
    "aspirin": _oral_drug(
        "aspirin", "antiplatelet",
        vd=0.15, cl=0.005, ka=0.03, bio=0.70, default_dose=325.0,
        pd=(
            PDParams(emax=0.0, ec50_mg_per_l=5.0, hill=1.0, target="sbp",
                     flags_above_frac={"antiplatelet": 0.2}),
        ),
        interactions={"warfarin": 0.7},
    ),
    "clopidogrel": _oral_drug(
        "clopidogrel", "P2Y12 inhibitor",
        vd=0.30, cl=0.008, ka=0.01, bio=0.50, default_dose=300.0,
        pd=(
            PDParams(emax=0.0, ec50_mg_per_l=0.5, hill=1.0, target="sbp",
                     flags_above_frac={"antiplatelet": 0.2}),
        ),
    ),
    "heparin": _iv_drug(
        "heparin", "anticoagulant",
        vd=0.07, cl=0.005, default_dose=5000.0, dose_unit="units",
        pd=(
            PDParams(emax=0.0, ec50_mg_per_l=0.5, hill=1.0, target="sbp",
                     flags_above_frac={"anticoagulated": 0.2}),
        ),
        toxic_cp=2.0, adverse_flags=frozenset({"bleeding"}),
    ),

    # ── Thrombolytics ──
    "streptokinase": DrugSpec(
        name="streptokinase", class_name="thrombolytic",
        pk=PKParams(vd_central_l_per_kg=0.05, cl_l_per_min_per_kg=0.002,
                    hepatic_fraction=0.3, renal_fraction=0.7),
        pd=(
            PDParams(emax=-8.0, ec50_mg_per_l=10.0, hill=1.0, target="hr",
                     flags_above_frac={"lysing": 0.3}),
        ),
        default_dose_mg=1500000.0, dose_unit="units",
        default_route=Route.IV_INFUSION,
        toxic_plasma_mg_per_l=50.0, adverse_flags=frozenset({"bleeding", "hypotension"}),
    ),

    # ── Antipyretic ──
    "paracetamol": _oral_drug(
        "paracetamol", "antipyretic analgesic",
        vd=0.15, cl=0.005, ka=0.02, bio=0.85, default_dose=1000.0,
        pd=(
            PDParams(emax=-0.8, ec50_mg_per_l=10.0, hill=1.0, target="temp_c"),
        ),
        toxic_cp=120.0, adverse_flags=frozenset({"hepatotoxicity"}),
    ),

    # ── Fluids (modelled as rapid-distribution 1c with short half-life) ──
    "iv_fluid_ns": _iv_drug(
        "iv_fluid_ns", "crystalloid",
        vd=0.30, cl=0.010, default_dose=500.0, dose_unit="ml",
        pd=(
            PDParams(emax=+12.0, ec50_mg_per_l=5.0, hill=1.0, target="sbp",
                     flags_above_frac={"volume_expansion": 0.2}),
            PDParams(emax=-3.0, ec50_mg_per_l=5.0, hill=1.0, target="hr"),
        ),
        toxic_cp=30.0, adverse_flags=frozenset({"fluid_overload"}),
    ),
    "iv_fluid_rl": _iv_drug(
        "iv_fluid_rl", "crystalloid (balanced)",
        vd=0.30, cl=0.010, default_dose=500.0, dose_unit="ml",
        pd=(
            PDParams(emax=+10.0, ec50_mg_per_l=5.0, hill=1.0, target="sbp",
                     flags_above_frac={"volume_expansion": 0.2}),
            PDParams(emax=-2.0, ec50_mg_per_l=5.0, hill=1.0, target="hr"),
        ),
        toxic_cp=30.0, adverse_flags=frozenset({"fluid_overload"}),
    ),

    # ── Glucose / insulin ──
    "glucose_25": _iv_drug(
        "glucose_25", "hypertonic glucose",
        vd=0.20, cl=0.030, default_dose=50.0, dose_unit="ml",
        pd=(
            PDParams(emax=+50.0, ec50_mg_per_l=50.0, hill=1.0, target="glucose",
                     flags_above_frac={"reverses_hypoglycemia": 0.2}),
        ),
    ),
    "insulin_regular": _iv_drug(
        "insulin_regular", "rapid insulin",
        vd=0.10, cl=0.020, default_dose=10.0, dose_unit="units",
        pd=(
            PDParams(emax=-40.0, ec50_mg_per_l=0.05, hill=1.5, target="glucose", ke0_per_min=0.01),
        ),
        toxic_cp=0.2, adverse_flags=frozenset({"hypoglycemia"}),
    ),
    "glucagon": DrugSpec(
        name="glucagon", class_name="hormone",
        pk=PKParams(vd_central_l_per_kg=0.15, cl_l_per_min_per_kg=0.010,
                    ka_per_min=0.05, bioavailability=1.0),
        pd=(
            PDParams(emax=+30.0, ec50_mg_per_l=0.5, hill=1.0, target="glucose"),
            PDParams(emax=+8.0, ec50_mg_per_l=0.5, hill=1.0, target="hr"),
        ),
        default_dose_mg=1.0, default_route=Route.IM,
    ),

    # ── Respiratory ──
    "salbutamol": DrugSpec(
        name="salbutamol", class_name="beta-2 agonist",
        pk=PKParams(vd_central_l_per_kg=0.20, cl_l_per_min_per_kg=0.010,
                    ka_per_min=0.10, bioavailability=0.20),
        pd=(
            PDParams(emax=+12.0, ec50_mg_per_l=0.01, hill=1.0, target="hr"),
            PDParams(emax=-3.0, ec50_mg_per_l=0.01, hill=1.0, target="rr"),
            PDParams(emax=+2.0, ec50_mg_per_l=0.01, hill=1.0, target="spo2",
                     flags_above_frac={"bronchodilation": 0.3}),
        ),
        default_dose_mg=5.0, default_route=Route.INHALED,
        toxic_plasma_mg_per_l=0.05, adverse_flags=frozenset({"tachycardia"}),
    ),
    "hydrocortisone": _iv_drug(
        "hydrocortisone", "corticosteroid",
        vd=0.30, cl=0.005, default_dose=100.0, protein_binding=0.90,
        pd=(
            PDParams(emax=0.0, ec50_mg_per_l=5.0, hill=1.0, target="sbp",
                     flags_above_frac={"anti_inflammatory": 0.2}),
        ),
    ),
    "magnesium_sulfate": _iv_drug(
        "magnesium_sulfate", "mineral / antiarrhythmic",
        vd=0.20, cl=0.005, default_dose=4000.0, dose_unit="mg",
        ren_frac=0.9, hep_frac=0.1,
        pd=(
            PDParams(emax=-6.0, ec50_mg_per_l=2.0, hill=1.0, target="sbp"),
            PDParams(emax=-4.0, ec50_mg_per_l=2.0, hill=1.0, target="hr"),
            PDParams(emax=-3.0, ec50_mg_per_l=2.0, hill=1.0, target="rr",
                     flags_above_frac={"smooth_muscle_relaxation": 0.3}),
        ),
        interactions={"morphine": 0.5, "fentanyl": 0.5},
        toxic_cp=6.0, adverse_flags=frozenset({"respiratory_depression", "respiratory_arrest"}),
    ),

    # ── Obstetric ──
    "oxytocin": _iv_drug(
        "oxytocin", "uterotonic",
        vd=0.15, cl=0.020, default_dose=10.0, dose_unit="units",
        pd=(
            PDParams(emax=+5.0, ec50_mg_per_l=0.01, hill=1.0, target="sbp",
                     flags_above_frac={"uterine_contraction": 0.3}),
            PDParams(emax=+3.0, ec50_mg_per_l=0.01, hill=1.0, target="hr"),
        ),
        toxic_cp=0.05, adverse_flags=frozenset({"hypertension", "tetany"}),
    ),
}


def get_drug(name: str) -> DrugSpec | None:
    return DRUG_REGISTRY.get(name.lower())
