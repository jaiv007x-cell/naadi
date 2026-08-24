"""
Pinned validation context for authoring compile/approve.

Compile at approval time uses the registry captured at submit, not live
`now()` catalogs. A retired action between IN_REVIEW and APPROVED must not
make the case unapprovable. The hash is what the approval row records as
"granted against registry state X".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from services.pratibimb.app.action_registry import (
    DEPRECATED_ACTION_IDS,
    DEPRECATED_ACTION_RETIRED_ON,
    KNOWN_ACTION_IDS,
)
from services.pratibimb.app.eval.matcher_registry import KNOWN_MATCHER_KINDS
from services.pratibimb.app.eval.rubric import NIRIKSHAK_VERSION
from services.pratibimb.authoring.constants import HARNESS_VERSION


@dataclass(frozen=True)
class RegistrySnapshot:
    action_ids: tuple[str, ...]
    matcher_kinds: tuple[str, ...]
    deprecated_action_ids: dict[str, str | None]
    retired_on: dict[str, str]
    guideline_versions: tuple[str, ...]
    nirikshak_version: str
    harness_version: str

    def resolve_action(self, action_id: str) -> str | None:
        if action_id in self.action_ids:
            return action_id
        return self.deprecated_action_ids.get(action_id)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "action_ids": list(self.action_ids),
            "matcher_kinds": list(self.matcher_kinds),
            "deprecated_action_ids": {
                k: self.deprecated_action_ids[k]
                for k in sorted(self.deprecated_action_ids)
            },
            "retired_on": {k: self.retired_on[k] for k in sorted(self.retired_on)},
            "guideline_versions": list(self.guideline_versions),
            "nirikshak_version": self.nirikshak_version,
            "harness_version": self.harness_version,
        }

    def validation_context_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RegistrySnapshot:
        deprecated = payload.get("deprecated_action_ids") or {}
        return cls(
            action_ids=tuple(payload.get("action_ids") or ()),
            matcher_kinds=tuple(payload.get("matcher_kinds") or ()),
            deprecated_action_ids={str(k): v for k, v in deprecated.items()},
            retired_on={str(k): str(v) for k, v in (payload.get("retired_on") or {}).items()},
            guideline_versions=tuple(payload.get("guideline_versions") or ()),
            nirikshak_version=str(payload.get("nirikshak_version") or ""),
            harness_version=str(payload.get("harness_version") or ""),
        )


def capture_registry_snapshot(blueprint_json: dict) -> RegistrySnapshot:
    provenance = blueprint_json.get("provenance") or {}
    guidelines = provenance.get("guideline_versions") or ()
    return RegistrySnapshot(
        action_ids=tuple(sorted(KNOWN_ACTION_IDS)),
        matcher_kinds=tuple(sorted(KNOWN_MATCHER_KINDS)),
        deprecated_action_ids=dict(DEPRECATED_ACTION_IDS),
        retired_on=dict(DEPRECATED_ACTION_RETIRED_ON),
        guideline_versions=tuple(str(g) for g in guidelines),
        nirikshak_version=NIRIKSHAK_VERSION,
        harness_version=HARNESS_VERSION,
    )
