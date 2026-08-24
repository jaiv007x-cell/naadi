"""
Migration audit vocabulary, shared by trace ingestion and case-schema upgrades.

Evidence that may one day anchor a credential cannot be transformed silently.
Anything a migrator discards, derives, or invents has to be countable years
later, so every migration returns a report alongside its payload.

The distinction that matters most is `inferred` vs `defaulted`:

- `inferred` fields were derived from data actually present in the source, so
  they can be validated automatically by a dry run.
- `defaulted` fields were filled from a schema default because the source said
  nothing. They carry no evidentiary weight and form the queue for human
  clinical review.

Collapsing the two into one "12 optional fields populated" number destroys
exactly the signal a reviewer needs.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MigrationReport:
    """Audit of one source object's migration to a newer contract."""

    source: str = ""
    target: str = ""
    consumed: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    inferred: dict[str, str] = field(default_factory=dict)
    defaulted: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # ── recorders ────────────────────────────────────────────────────────

    def consume(self, kind: str, n: int = 1) -> None:
        self.consumed[kind] += n

    def drop(self, kind: str, n: int = 1) -> None:
        self.dropped[kind] += n

    def infer(self, field_name: str, derivation: str) -> None:
        """Record a field derived from data present in the source."""
        self.inferred[field_name] = derivation

    def default(self, field_name: str, value: Any) -> None:
        """Record a field filled from a schema default. Needs human review."""
        self.defaulted[field_name] = value

    def note(self, text: str) -> None:
        self.notes.append(text)

    # ── views ────────────────────────────────────────────────────────────

    @property
    def total_consumed(self) -> int:
        return sum(self.consumed.values())

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    @property
    def is_lossless(self) -> bool:
        return self.total_dropped == 0

    @property
    def needs_review(self) -> bool:
        """True when a human must confirm the defaulted fields."""
        return bool(self.defaulted)

    def _breakdown(self, counter: Counter) -> str:
        return ", ".join(f"{kind}: {n}" for kind, n in sorted(counter.most_common()))

    def audit_line(self) -> str:
        """One-line drop audit, e.g. `Dropped: 3 - avatar_frame: 2, ui_focus: 1`."""
        if self.is_lossless:
            return "Dropped: 0"
        return f"Dropped: {self.total_dropped} - {self._breakdown(self.dropped)}"

    def field_line(self) -> str:
        return (
            f"Inferred from source: {len(self.inferred)}; "
            f"Defaulted (needs review): {len(self.defaulted)}"
        )

    def summary(self) -> str:
        lines = [
            f"Migration {self.source or '?'} -> {self.target or '?'}",
            f"Consumed: {self.total_consumed}"
            + (f" - {self._breakdown(self.consumed)}" if self.consumed else ""),
            self.audit_line(),
            f"Inferred from source: {len(self.inferred)}",
            f"Defaulted (needs review): {len(self.defaulted)}",
        ]
        for name, value in sorted(self.defaulted.items()):
            lines.append(f"  review: {name} = {value!r}")
        lines.extend(f"  note: {n}" for n in self.notes)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "consumed": dict(sorted(self.consumed.items())),
            "dropped": dict(sorted(self.dropped.items())),
            "total_consumed": self.total_consumed,
            "total_dropped": self.total_dropped,
            "inferred": dict(sorted(self.inferred.items())),
            "defaulted": {k: self.defaulted[k] for k in sorted(self.defaulted)},
            "needs_review": self.needs_review,
            "is_lossless": self.is_lossless,
            "notes": list(self.notes),
        }
