"""
The single trace contract.

Event-sourced record of everything the grader is allowed to look at. All
producers (physiology engine, session runner, pharmacology engine) record
typed events; all consumers (Nirikshak, ledger, scorer) call typed queries.

No duck-typing, no `getattr(trace, "flag_history", [])`, no dict spelunking.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from shared.schemas.migration import MigrationReport

TraceEventKind = Literal[
    "drug_admin", "flag_set", "flag_clear", "order_placed",
    "diagnosis_stated", "escalation", "vital_ack", "finding_ack",
    "symptom_elicited", "physical_sign_identified", "utterance",
]

RecognitionKind = Literal["vital", "finding", "symptom", "physical_sign"]


@dataclass(frozen=True)
class DrugAdminEvent:
    drug_id: str
    dose: float
    dose_unit: str          # "mg", "mcg", "IU"
    route: str              # "IV", "IM", "PO", "SL", "SC", "IN", "NEB"
    t_s: float


@dataclass(frozen=True)
class FlagEvent:
    flag: str
    active: bool            # True=set, False=clear
    t_s: float


@dataclass(frozen=True)
class OrderEvent:
    order_id: str           # "ecg_12l", "troponin", "cxr"
    t_s: float
    params: dict = None     # optional order detail, e.g. {"lpm": 8}

    def __post_init__(self) -> None:
        if self.params is None:
            object.__setattr__(self, "params", {})


@dataclass(frozen=True)
class DiagnosisEvent:
    dx: str                 # normalized diagnosis token
    confidence: float
    t_s: float


@dataclass(frozen=True)
class RecognitionEvent:
    kind: RecognitionKind
    token: str              # "spo2_low", "stridor", "urticaria", "wheeze"
    t_s: float


@dataclass(frozen=True)
class EscalationEvent:
    target: str             # "physician_on_call", "code_blue", "rapid_response"
    t_s: float


@dataclass(frozen=True)
class HandoffEvent:
    fields: frozenset       # SBAR field names present, e.g. {"situation", ...}
    target: str
    t_s: float


class PhysioTrace:
    """
    Single source of truth for anything the grader looks at.
    Event-sourced; all views are derived.
    """

    def __init__(self, case_id: str = "", case_version: str = "0.0.0") -> None:
        self.case_id = case_id
        self.case_version = case_version
        self._drugs: list[DrugAdminEvent] = []
        self._flags: list[FlagEvent] = []
        self._orders: list[OrderEvent] = []
        self._dx: list[DiagnosisEvent] = []
        self._recog: list[RecognitionEvent] = []
        self._escal: list[EscalationEvent] = []
        self._handoffs: list[HandoffEvent] = []
        self._t_end: float = 0.0

    # ── recorders ────────────────────────────────────────────────────────

    def record_drug(self, e: DrugAdminEvent) -> None:
        self._drugs.append(e)
        self._advance(e.t_s)

    def record_flag(self, e: FlagEvent) -> None:
        self._flags.append(e)
        self._advance(e.t_s)

    def record_order(self, e: OrderEvent) -> None:
        self._orders.append(e)
        self._advance(e.t_s)

    def record_diagnosis(self, e: DiagnosisEvent) -> None:
        self._dx.append(e)
        self._advance(e.t_s)

    def record_recognition(self, e: RecognitionEvent) -> None:
        self._recog.append(e)
        self._advance(e.t_s)

    def record_escalation(self, e: EscalationEvent) -> None:
        self._escal.append(e)
        self._advance(e.t_s)

    def record_handoff(self, e: HandoffEvent) -> None:
        self._handoffs.append(e)
        self._advance(e.t_s)

    def _advance(self, t: float) -> None:
        if t > self._t_end:
            self._t_end = t

    @property
    def duration_s(self) -> float:
        return self._t_end

    # ── queries ──────────────────────────────────────────────────────────

    def drug_admins(self, drug_id: str | None = None) -> list[DrugAdminEvent]:
        if drug_id is None:
            return list(self._drugs)
        key = _norm(drug_id)
        return [d for d in self._drugs if _norm(d.drug_id) == key]

    def flag_events(self, flag: str | None = None) -> list[FlagEvent]:
        if flag is None:
            return list(self._flags)
        return [f for f in self._flags if f.flag == flag]

    def flag_spans(self, flag: str) -> list[tuple[float, float]]:
        """Closed intervals during which `flag` was active. Open spans close at t_end."""
        spans: list[tuple[float, float]] = []
        open_at: float | None = None
        for e in self.flag_events(flag):
            if e.active and open_at is None:
                open_at = e.t_s
            elif not e.active and open_at is not None:
                spans.append((open_at, e.t_s))
                open_at = None
        if open_at is not None:
            spans.append((open_at, self._t_end))
        return spans

    def flag_set_at(self, flag: str) -> float | None:
        for e in self._flags:
            if e.flag == flag and e.active:
                return e.t_s
        return None

    def flag_cleared_at(self, flag: str) -> float | None:
        for e in self._flags:
            if e.flag == flag and not e.active:
                return e.t_s
        return None

    def flag_active_at(self, flag: str, t: float) -> bool:
        return any(lo <= t <= hi for lo, hi in self.flag_spans(flag))

    def orders(self, order_id: str | None = None) -> list[OrderEvent]:
        if order_id is None:
            return list(self._orders)
        key = order_id.lower()
        return [o for o in self._orders if o.order_id.lower() == key]

    def diagnoses(self, dx: str | None = None) -> list[DiagnosisEvent]:
        if dx is None:
            return list(self._dx)
        key = dx.lower()
        return [d for d in self._dx if d.dx.lower() == key]

    def recognitions(
        self, kind: str | None = None, token: str | None = None
    ) -> list[RecognitionEvent]:
        return [
            r for r in self._recog
            if (kind is None or r.kind == kind)
            and (token is None or r.token.lower() == token.lower())
        ]

    def escalations(self, target: str | None = None) -> list[EscalationEvent]:
        if target is None:
            return list(self._escal)
        key = target.lower()
        return [e for e in self._escal if e.target.lower() == key]

    def handoffs(self, target: str | None = None) -> list[HandoffEvent]:
        if target is None:
            return list(self._handoffs)
        key = target.lower()
        return [h for h in self._handoffs if h.target.lower() == key]

    # ── canonical serialization for hashing ──────────────────────────────

    def to_canonical(self) -> dict:
        """Deterministic dict form. Key order is fixed; used for manifest hashing."""
        return {
            "case_id": self.case_id,
            "case_version": self.case_version,
            "duration_s": self._t_end,
            "drugs": [asdict(d) for d in self._drugs],
            "flags": [asdict(f) for f in self._flags],
            "orders": [asdict(o) for o in self._orders],
            "dx": [asdict(d) for d in self._dx],
            "recog": [asdict(r) for r in self._recog],
            "escal": [asdict(e) for e in self._escal],
            "handoffs": [
                {"fields": sorted(h.fields), "target": h.target, "t_s": h.t_s}
                for h in self._handoffs
            ],
        }

    # ── legacy bridge ────────────────────────────────────────────────────

    @classmethod
    def from_legacy(
        cls,
        legacy,
        case_id: str = "",
        case_version: str = "0.0.0",
        *,
        strict: bool = False,
    ) -> LegacyTraceMigration:
        """
        Convert the older `physio.state.PhysioTrace` (dict-event based) into the
        typed contract, returning the trace together with an audit of what was
        consumed, inferred, defaulted, and discarded.

        Callers get a `LegacyTraceMigration` rather than a bare trace so that
        discarded evidence is impossible to overlook.

        With `strict=True` an unrecognized event kind raises
        `UnknownLegacyEventKind` instead of being counted as a drop. Use it in
        corpus migration and tests, where an unknown kind means the migrator is
        out of date; leave it off for live sessions, where UI chatter in the
        legacy event stream is expected and harmless.
        """
        t = cls(case_id=case_id, case_version=case_version)
        report = MigrationReport(source="physio.state.PhysioTrace", target="shared.schemas.trace.PhysioTrace")

        if not case_id:
            report.default("case_id", "")
        if case_version == "0.0.0":
            report.default("case_version", "0.0.0")

        for idx, ev in enumerate(getattr(legacy, "events", []) or []):
            kind = ev.get("kind", "") or "unspecified"

            if "t" in ev:
                t_s = float(ev["t"])
            elif "t_min" in ev:
                t_s = float(ev["t_min"]) * 60.0
                report.infer(f"events[{idx}].t_s", "t_min * 60")
            else:
                t_s = 0.0
                report.default(f"events[{idx}].t_s", 0.0)

            if kind in _DRUG_KINDS:
                payload = ev.get("payload", {})
                dose = ev.get("amount_mg", ev.get("dose_mg", ev.get("dose", payload.get("dose"))))
                if dose is None:
                    dose = 0.0
                    report.default(f"events[{idx}].dose", 0.0)
                unit = ev.get("dose_unit", payload.get("dose_unit"))
                if unit is None:
                    unit = "mg"
                    report.infer(f"events[{idx}].dose_unit", "assumed mg for a legacy amount_mg field")
                route = str(ev.get("route", payload.get("route", "")) or "")
                if not route:
                    report.default(f"events[{idx}].route", "")
                t.record_drug(DrugAdminEvent(
                    drug_id=ev.get("drug", payload.get("drug", "")),
                    dose=float(dose),
                    dose_unit=unit,
                    route=route.upper(),
                    t_s=t_s,
                ))
                report.consume(kind)
            elif kind == "order":
                payload = ev.get("payload", {})
                t.record_order(OrderEvent(
                    order_id=ev.get("order", payload.get("order", "")),
                    t_s=t_s,
                    params={k: v for k, v in payload.items() if k != "order"},
                ))
                report.consume(kind)
            elif kind == "diagnosis":
                payload = ev.get("payload", {})
                confidence = ev.get("confidence", payload.get("confidence"))
                if confidence is None:
                    confidence = 1.0
                    report.default(f"events[{idx}].confidence", 1.0)
                t.record_diagnosis(DiagnosisEvent(
                    dx=ev.get("dx", payload.get("dx", "")),
                    confidence=float(confidence),
                    t_s=t_s,
                ))
                report.consume(kind)
            elif kind == "escalation":
                payload = ev.get("payload", {})
                t.record_escalation(EscalationEvent(
                    target=ev.get("target", payload.get("to", payload.get("to_role", ""))),
                    t_s=t_s,
                ))
                report.consume(kind)
            else:
                if strict:
                    raise UnknownLegacyEventKind(kind)
                report.drop(kind)

        for fh in getattr(legacy, "flag_history", []) or []:
            active = fh.get("set", fh.get("active"))
            if active is None:
                active = False
                report.default(f"flag_history[{fh.get('flag', '?')}].active", False)
            t.record_flag(FlagEvent(
                flag=fh.get("flag", ""),
                active=bool(active),
                t_s=float(fh.get("t", 0.0)),
            ))
            report.consume("flag_history")

        snapshots = getattr(legacy, "snapshots", []) or []
        if snapshots:
            t._advance(float(getattr(snapshots[-1], "t", 0.0)))
            report.infer("duration_s", "last physiology snapshot timestamp")

        return LegacyTraceMigration(trace=t, report=report)


_DRUG_KINDS = frozenset({"dose", "medication", "drug"})


class UnknownLegacyEventKind(ValueError):
    """Raised by `from_legacy(strict=True)` when an event kind is unrecognized."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"unknown legacy event kind: {kind}")


@dataclass(frozen=True)
class LegacyTraceMigration:
    """A migrated trace plus the audit of how it was produced."""

    trace: PhysioTrace
    report: MigrationReport

    def __iter__(self):
        """Allow `trace, report = PhysioTrace.from_legacy(...)`."""
        return iter((self.trace, self.report))

    @property
    def accepted_events(self) -> int:
        return self.report.total_consumed

    @property
    def dropped_events(self) -> int:
        return self.report.total_dropped

    @property
    def dropped_kinds(self) -> dict[str, int]:
        return dict(self.report.dropped)


def _norm(s: str) -> str:
    """Case- and punctuation-insensitive identifier normalization."""
    return "".join(ch for ch in s.lower() if ch.isalnum())
