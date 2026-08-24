"""
Derives PhysiologyEngine behavioral version from observable output,
not source assertion.

Runs a fixed suite of trajectories against the engine using a deterministic
SimClock, canonicalizes each PhysioTrace, and hashes the concatenation.
The resulting hash IS the version.

Contract:
    - Probe suite is versioned separately (PROBE_SUITE_VERSION).
    - Ledger stores PhysiologyManifest, not just a version string.
    - Consumers can re-run the probe against a checked-out engine commit
      and verify historical consistency.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

from shared.schemas.case import CaseBlueprint
from services.pratibimb.app.case_gen.sampler import load_probe_case
from services.pratibimb.app.physio.state import PhysiologyEngine, SimClock

PROBE_SUITE_VERSION = "1.0.0"


@dataclass(frozen=True)
class ProbeAction:
    t_offset_s: float
    action: str
    params: dict


@dataclass(frozen=True)
class ProbeTrajectory:
    name: str
    case_id: str
    actions: tuple[ProbeAction, ...]
    duration_s: float


PROBE_SUITE: tuple[ProbeTrajectory, ...] = (
    ProbeTrajectory(
        name="stemi_untreated_deterioration",
        case_id="probe.stemi.inferior.v1",
        actions=(),
        duration_s=1800.0,
    ),
    ProbeTrajectory(
        name="stemi_aspirin_only",
        case_id="probe.stemi.inferior.v1",
        actions=(
            ProbeAction(60.0, "give_aspirin_325_chewed", {}),
        ),
        duration_s=1800.0,
    ),
    ProbeTrajectory(
        name="stemi_aspirin_then_morphine",
        case_id="probe.stemi.inferior.v1",
        actions=(
            ProbeAction(60.0, "give_aspirin_325_chewed", {}),
            ProbeAction(300.0, "give_morphine", {}),
        ),
        duration_s=1800.0,
    ),
)


@dataclass(frozen=True)
class PhysiologyManifest:
    """What the ledger stores. Version string is a summary, not the truth."""
    engine_class: str
    probe_suite_version: str
    behavior_hash: str
    per_trajectory_hashes: tuple[tuple[str, str], ...]

    @property
    def version(self) -> str:
        return f"physio@{self.behavior_hash[:12]}"

    def to_ledger_dict(self) -> dict:
        return {
            "engine_class": self.engine_class,
            "probe_suite_version": self.probe_suite_version,
            "behavior_hash": self.behavior_hash,
            "per_trajectory_hashes": list(self.per_trajectory_hashes),
        }


def _canonical_json_bytes(obj: object) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _run_trajectory(case: CaseBlueprint, traj: ProbeTrajectory) -> dict:
    clock = SimClock(0.0)
    engine = PhysiologyEngine(case, clock=clock)

    action_cursor = 0
    sorted_actions = sorted(traj.actions, key=lambda a: a.t_offset_s)

    while clock.now() - engine.state.started_at < traj.duration_s and not engine.state.dead:
        next_action_t = (
            sorted_actions[action_cursor].t_offset_s
            if action_cursor < len(sorted_actions)
            else traj.duration_s
        )
        target = min(next_action_t, traj.duration_s)
        engine.tick_to(engine.state.started_at + target)

        if (
            action_cursor < len(sorted_actions)
            and clock.now() - engine.state.started_at >= sorted_actions[action_cursor].t_offset_s
        ):
            act = sorted_actions[action_cursor]
            engine.apply_action(act.action, act.params or None)
            action_cursor += 1

    trace = engine.trace()
    return trace.to_canonical()


def _hash_canonical(canonical: dict) -> str:
    return hashlib.sha256(_canonical_json_bytes(canonical)).hexdigest()


def compute_manifest(
    engine_cls: type[PhysiologyEngine] = PhysiologyEngine,
) -> PhysiologyManifest:
    case = load_probe_case("probe.stemi.inferior.v1", harness=True)

    per: list[tuple[str, str]] = []
    for traj in PROBE_SUITE:
        canonical = _run_trajectory(case, traj)
        h = _hash_canonical(canonical)
        per.append((traj.name, h))

    per_tuple = tuple(per)
    combined = hashlib.sha256(
        b"\n".join(f"{name}:{h}".encode() for name, h in per_tuple)
    ).hexdigest()

    return PhysiologyManifest(
        engine_class=f"{engine_cls.__module__}.{engine_cls.__qualname__}",
        probe_suite_version=PROBE_SUITE_VERSION,
        behavior_hash=combined,
        per_trajectory_hashes=per_tuple,
    )


def _lazy_manifest() -> PhysiologyManifest:
    """Deferred computation — call once per process, not at import time."""
    global _MANIFEST, _VERSION
    if _MANIFEST is None:
        _MANIFEST = compute_manifest()
        _VERSION = _MANIFEST.version
    return _MANIFEST


_MANIFEST: PhysiologyManifest | None = None
_VERSION: str | None = None


def get_manifest() -> PhysiologyManifest:
    return _lazy_manifest()


def get_version() -> str:
    _lazy_manifest()
    assert _VERSION is not None
    return _VERSION
