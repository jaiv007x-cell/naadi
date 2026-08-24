"""F.a projection-layer redaction — allowlist enforced at build time (I-F-5)."""
from __future__ import annotations

import copy
import json
from typing import Any

# Grep targets for tests — fields that must never appear in NCVET export JSON.
# I-F-5 (wire grep): tests grep the full serialized response string (not top-level
# keys only) so nested dicts and arrays-of-objects are covered.
#
# I-F-5 (flat JSON co-requirement): projection values MUST stay plain JSON
# scalars/structures — no base64/JWT-encoded blobs. Encoded blobs would render
# field-name grep blind because excluded keys would not appear literally in the
# wire bytes. This comment and the grep test in test_authoring_f_a.py are one
# invariant across two files; do not split them.
EXCLUDED_FIELD_NAMES = frozenset({
    "demographics",
    "name",
    "verbatim",
    "chief_complaint_verbatim",
    "persona_notes",
    "family_personas",
})


def _strip_excluded_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key in EXCLUDED_FIELD_NAMES:
                continue
            out[key] = _strip_excluded_keys(value)
        return out
    if isinstance(obj, list):
        return [_strip_excluded_keys(item) for item in obj]
    return obj


def redact_case_context_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """
    Build NCVET case context from corpus envelope — demographics never selected.

    Entire ``patient`` subtree is omitted (contains demographics). Provenance,
    fixtures summary, blueprint identity/targeting/clinical_truth (without
    free-text name fields) may remain per allowlist.
    """
    redacted = copy.deepcopy(envelope)
    blueprint = redacted.get("blueprint")
    if isinstance(blueprint, dict):
        blueprint.pop("patient", None)
        interaction = blueprint.get("interaction")
        if isinstance(interaction, dict):
            interaction.pop("family_personas", None)
        redacted["blueprint"] = _strip_excluded_keys(blueprint)
    return _strip_excluded_keys(redacted)


def redact_case_context_json(envelope_json: str) -> str:
    envelope = json.loads(envelope_json)
    return json.dumps(redact_case_context_envelope(envelope), sort_keys=True)
