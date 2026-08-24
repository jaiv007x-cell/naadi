"""
Migrate v1 case dicts to `CaseBlueprintV2`.

Reads either a directory of `*.json` case files or a single JSON file containing
a list of cases (which is how `case_gen/seed_corpus.json` is shaped).

Every field the migrator populates is classified, because "12 optional fields
populated" is not an auditable statement:

- inferred from source  -> derived from data actually present in the v1 dict;
                           validate these with an automated dry run.
- defaulted, needs review -> filled from a schema default because v1 said
                           nothing; this is the clinical reviewer's queue.

Cases are also tiered by provenance completeness rather than by assertion, so
nothing reaches summative use without a named reviewer and a grading blueprint.

Usage:
    python -m scripts.migrate_cases_v1_to_v2 <corpus-path> [--out report.json]
    python -m scripts.migrate_cases_v1_to_v2 <corpus-path> --strict
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from services.pratibimb.app.action_registry import (
    DEPRECATED_ACTION_IDS,
    KNOWN_ACTION_IDS,
    resolve as resolve_action,
)
from shared.schemas.case import Demographics, Language, Vital
from shared.schemas.case_v2 import (
    AssessmentMode,
    CaseBlueprintV2,
    CaseIdentity,
    CaseTargeting,
    ClinicalTruth,
    CorpusTier,
    EnvironmentSpec,
    InteractionSpec,
    PatientPersona,
    PhysiologySpec,
    Provenance,
)
from shared.schemas.migration import MigrationReport

PHYSIO_ENGINE_VERSION = "0.2.0"
DEFAULT_SETTING = "opd"


# ── per-case result ───────────────────────────────────────────────────────────

@dataclass
class CaseMigrationResult:
    """Outcome for one v1 case."""

    case_id: str
    tier: CorpusTier
    converted: bool
    report: MigrationReport
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blueprint: CaseBlueprintV2 | None = None

    @property
    def inferred_from_source(self) -> list[str]:
        return sorted(self.report.inferred)

    @property
    def defaulted_needs_review(self) -> list[str]:
        return sorted(self.report.defaulted)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "tier": self.tier.value,
            "converted": self.converted,
            "inferred_from_source": self.inferred_from_source,
            "defaulted_needs_review": self.defaulted_needs_review,
            "missing_required": list(self.missing_required),
            "warnings": list(self.warnings),
            "notes": list(self.report.notes),
            "content_hash": (
                self.blueprint.provenance.content_hash if self.blueprint else None
            ),
        }


# ── corpus-level report ───────────────────────────────────────────────────────

@dataclass
class CorpusMigrationReport:
    """
    Aggregate across a corpus.

    Named distinctly from `shared.schemas.migration.MigrationReport`, which
    audits a single object's field origins and is reused here per case.
    """

    examined: int = 0
    converted: int = 0
    by_tier: dict[str, int] = field(default_factory=lambda: {
        "gold": 0, "silver": 0, "bronze": 0, "draft": 0,
    })
    missing_required_total: int = 0
    inferred_total: int = 0
    defaulted_total: int = 0
    missing_provenance: int = 0
    missing_grading_blueprint: int = 0
    obsolete_action_ids: int = 0
    results: list[CaseMigrationResult] = field(default_factory=list)

    def add(self, result: CaseMigrationResult) -> None:
        self.examined += 1
        if result.converted:
            self.converted += 1
        self.by_tier[result.tier.value] += 1
        self.missing_required_total += len(result.missing_required)
        self.inferred_total += len(result.report.inferred)
        self.defaulted_total += len(result.report.defaulted)
        if result.blueprint and not result.blueprint.provenance.is_clinically_reviewed:
            self.missing_provenance += 1
        if result.blueprint and result.blueprint.grading_blueprint is None:
            self.missing_grading_blueprint += 1
        if any(w.startswith("obsolete_action_ids") for w in result.warnings):
            self.obsolete_action_ids += 1
        self.results.append(result)

    @property
    def review_queue(self) -> list[CaseMigrationResult]:
        """Cases a clinician must look at before promotion."""
        return [r for r in self.results if r.defaulted_needs_review or r.missing_required]

    def render(self) -> str:
        lines = [
            f"{self.examined} cases examined",
            f"{self.converted} converted",
            "",
            f"{self.by_tier['gold']} GOLD-candidate",
            f"{self.by_tier['silver']} SILVER",
            f"{self.by_tier['bronze']} BRONZE",
            f"{self.by_tier['draft']} DRAFT",
            "",
            f"{self.missing_required_total} unknown required fields",
            f"Inferred from source: {self.inferred_total}",
            f"Defaulted (needs review): {self.defaulted_total}",
            f"{self.missing_provenance} cases missing clinical provenance",
            f"{self.missing_grading_blueprint} cases missing grading blueprint",
            f"{self.obsolete_action_ids} cases contain obsolete action identifiers",
        ]
        if self.review_queue:
            lines.append("")
            lines.append("Clinical review queue:")
            for r in self.review_queue:
                detail = ", ".join(r.defaulted_needs_review[:4])
                more = "" if len(r.defaulted_needs_review) <= 4 else f" (+{len(r.defaulted_needs_review) - 4} more)"
                lines.append(f"  {r.case_id} [{r.tier.value}]: {detail}{more}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "examined": self.examined,
            "converted": self.converted,
            "by_tier": dict(self.by_tier),
            "missing_required_total": self.missing_required_total,
            "inferred_total": self.inferred_total,
            "defaulted_total": self.defaulted_total,
            "missing_provenance": self.missing_provenance,
            "missing_grading_blueprint": self.missing_grading_blueprint,
            "obsolete_action_ids": self.obsolete_action_ids,
            "results": [r.to_dict() for r in self.results],
        }


# ── tiering ───────────────────────────────────────────────────────────────────

def classify_tier(v1: dict) -> CorpusTier:
    """
    Derive readiness from provenance completeness.

    GOLD needs all three legs of defensibility: a named clinical reviewer, a
    grading blueprint, and pinned guideline versions. Anything less cannot
    support a credential claim.
    """
    has_reviewer = bool(v1.get("clinical_reviewer"))
    has_grading = bool(v1.get("grading_blueprint"))
    has_guidelines = bool(v1.get("guideline_versions"))

    if has_reviewer and has_grading and has_guidelines:
        return CorpusTier.GOLD
    if has_grading and (has_reviewer or has_guidelines):
        return CorpusTier.SILVER
    if has_grading:
        return CorpusTier.BRONZE
    return CorpusTier.DRAFT


# ── field helpers ─────────────────────────────────────────────────────────────

def _take(
    v1: dict, key: str, target: str, report: MigrationReport, default: Any
) -> Any:
    """Read `key`, recording whether the result came from source or a default."""
    if key in v1 and v1[key] not in (None, "", [], {}):
        report.infer(target, f"v1.{key}")
        return v1[key]
    report.default(target, default)
    return default


def _tuple(value: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(str(v) for v in (value or ()))


# ── the migration ─────────────────────────────────────────────────────────────

def migrate_case_v1_to_v2(
    v1: dict,
    known_action_ids: frozenset[str] | set[str] = KNOWN_ACTION_IDS,
) -> CaseMigrationResult:
    report = MigrationReport(source="CaseBlueprint(v1)", target="CaseBlueprintV2")
    warnings: list[str] = []
    missing: list[str] = []

    case_id = v1.get("case_id")
    if not case_id:
        missing.append("identity.case_id")

    hidden = v1.get("hidden", {}) or {}
    if not hidden.get("primary_diagnosis"):
        missing.append("clinical_truth.primary_diagnosis")

    # ── identity ─────────────────────────────────────────────────────────
    version = _take(v1, "version", "identity.version", report, "1.0.0")
    title = _take(
        v1, "title", "identity.title", report,
        hidden.get("primary_diagnosis", "Untitled case"),
    )
    if "assessment_mode" in v1:
        mode = AssessmentMode(v1["assessment_mode"])
        report.infer("identity.assessment_mode", "v1.assessment_mode")
    else:
        # Defaulting to PRACTICE is the safe direction: it cannot promote an
        # unreviewed case into credential-bearing use.
        mode = AssessmentMode.PRACTICE
        report.default("identity.assessment_mode", mode.value)

    tier = classify_tier(v1)
    report.infer("identity.corpus_tier", "derived from provenance completeness")

    # ── targeting ────────────────────────────────────────────────────────
    difficulty = _take(v1, "difficulty", "targeting.difficulty", report, 0.5)
    competencies = _tuple(
        _take(v1, "competency_tags", "targeting.competencies", report, [])
    )
    roles = _tuple(_take(v1, "roles", "targeting.roles", report, ["gnm"]))
    prerequisites = _tuple(
        _take(v1, "prerequisites", "targeting.prerequisites", report, [])
    )

    # ── patient ──────────────────────────────────────────────────────────
    demo_raw = v1.get("demographics")
    if demo_raw:
        demographics = Demographics(**demo_raw)
        report.infer("patient.demographics", "v1.demographics")
    else:
        missing.append("patient.demographics")
        demographics = None

    language = (
        demographics.native_language if demographics else Language.HINDI
    )
    if demographics:
        report.infer("patient.language", "v1.demographics.native_language")
    else:
        report.default("patient.language", Language.HINDI.value)

    voice_profile = _take(
        v1, "persona_voice_id", "patient.voice_profile", report, "default_male_mr"
    )
    literacy = (
        demographics.education_years if demographics
        else _take(v1, "education_years", "patient.literacy_years", report, 8)
    )
    if demographics:
        report.infer("patient.literacy_years", "v1.demographics.education_years")

    # ── clinical truth ───────────────────────────────────────────────────
    for key, target in (
        ("primary_diagnosis", "clinical_truth.primary_diagnosis"),
        ("icd10", "clinical_truth.icd10"),
        ("onset_minutes_ago", "clinical_truth.onset_minutes_ago"),
        ("symptoms_present", "clinical_truth.symptoms_present"),
        ("symptoms_absent", "clinical_truth.absent_findings"),
        ("allergies", "clinical_truth.allergies"),
        ("current_meds", "clinical_truth.medications"),
    ):
        value = hidden.get(key)
        if value not in (None, "", [], {}):
            report.infer(target, f"v1.hidden.{key}")
        elif key in hidden:
            # Present but empty. For allergies the difference between "confirmed
            # none" and "never asked" is clinically load-bearing, so an empty
            # list still goes to the reviewer rather than being taken as fact.
            report.default(target, value)
            report.note(f"{target} recorded as empty in v1; confirm it means 'none', not 'unknown'")
        else:
            report.default(target, None)

    # v1 has no differentials or physical findings; both are review items.
    differentials = _tuple(v1.get("differentials"))
    if not differentials:
        report.default("clinical_truth.differentials", [])
    findings_present = _tuple(v1.get("findings_present"))
    if not findings_present:
        report.default("clinical_truth.findings_present", [])

    clinical_truth = ClinicalTruth(
        primary_diagnosis=hidden.get("primary_diagnosis", ""),
        icd10=hidden.get("icd10", ""),
        differentials=differentials,
        symptoms_present=_tuple(hidden.get("symptoms_present")),
        findings_present=findings_present,
        absent_findings=_tuple(hidden.get("symptoms_absent")),
        allergies=_tuple(hidden.get("allergies")),
        medications=_tuple(hidden.get("current_meds")),
        onset_minutes_ago=int(hidden.get("onset_minutes_ago", 0) or 0),
    )

    # ── environment ──────────────────────────────────────────────────────
    setting = _take(v1, "setting", "environment.setting", report, DEFAULT_SETTING)
    resources = _tuple(
        _take(v1, "resources_available", "environment.resources", report, [])
    )
    distractors = _tuple(hidden.get("red_herrings"))
    if distractors:
        report.infer("environment.distractors", "v1.hidden.red_herrings")
    else:
        report.default("environment.distractors", [])

    difficulty_parameters: dict[str, Any] = {}
    if "time_pressure_seconds" in v1:
        difficulty_parameters["time_pressure_seconds"] = v1["time_pressure_seconds"]
        report.infer(
            "environment.difficulty_parameters.time_pressure_seconds",
            "v1.time_pressure_seconds",
        )
    else:
        report.default("environment.difficulty_parameters.time_pressure_seconds", 900)
        difficulty_parameters["time_pressure_seconds"] = 900

    # ── physiology ───────────────────────────────────────────────────────
    vitals_raw = v1.get("baseline_vitals")
    if vitals_raw:
        initial_vitals = Vital(**vitals_raw)
        report.infer("physiology.initial_vitals", "v1.baseline_vitals")
    else:
        missing.append("physiology.initial_vitals")
        initial_vitals = None
    report.default("physiology.deterioration_rules", [])
    report.infer("physiology.engine_version", "pinned migrator constant")

    # ── interaction ──────────────────────────────────────────────────────
    allowed = _tuple(hidden.get("symptoms_present"))
    hidden_facts = _tuple(
        list(hidden.get("comorbidities", ())) + list(hidden.get("social_history", {}).keys())
    )
    family_present = v1.get("family_present")
    if family_present is None:
        report.default("interaction.family_personas", [])
        family_personas: tuple[str, ...] = ()
    else:
        report.infer("interaction.family_personas", "v1.family_present")
        family_personas = ("attendant",) if family_present else ()
    report.default("interaction.dialogue_constraints", [])

    # ── action ids ───────────────────────────────────────────────────────
    expected_actions = list(v1.get("expected_actions", ()) or ())
    obsolete = [a for a in expected_actions if a not in known_action_ids]
    if obsolete:
        rewritable = {a: resolve_action(a) for a in obsolete}
        retired = [a for a, r in rewritable.items() if r is None]
        renamed = {a: r for a, r in rewritable.items() if r is not None}
        warnings.append(f"obsolete_action_ids: {sorted(obsolete)}")
        if renamed:
            warnings.append(f"rewritable_action_ids: {renamed}")
        if retired:
            warnings.append(f"retired_action_ids: {sorted(retired)}")

    # ── provenance ───────────────────────────────────────────────────────
    reviewer = v1.get("clinical_reviewer")
    if reviewer:
        report.infer("provenance.clinical_reviewer", "v1.clinical_reviewer")
    else:
        report.default("provenance.clinical_reviewer", None)
    reviewed_at_raw = v1.get("reviewed_at")
    reviewed_at = (
        datetime.fromisoformat(reviewed_at_raw) if reviewed_at_raw else None
    )
    author = _take(v1, "author", "provenance.author", report, "unknown")
    sources = _tuple(_take(v1, "sources", "provenance.sources", report, []))
    guidelines = _tuple(
        _take(v1, "guideline_versions", "provenance.guideline_versions", report, [])
    )

    # A case missing its identity, patient, or physiology cannot be assembled.
    if missing:
        return CaseMigrationResult(
            case_id=case_id or "<unknown>",
            tier=tier,
            converted=False,
            report=report,
            missing_required=missing,
            warnings=warnings,
            blueprint=None,
        )

    blueprint = CaseBlueprintV2(
        identity=CaseIdentity(
            case_id=case_id,
            version=str(version),
            title=str(title),
            corpus_tier=tier,
            assessment_mode=mode,
        ),
        targeting=CaseTargeting(
            roles=roles,
            competencies=competencies,
            difficulty=float(difficulty),
            prerequisites=prerequisites,
        ),
        patient=PatientPersona(
            demographics=demographics,
            language=language,
            voice_profile=str(voice_profile),
            literacy_years=int(literacy),
        ),
        clinical_truth=clinical_truth,
        environment=EnvironmentSpec(
            setting=str(setting),
            resources=resources,
            distractors=distractors,
            difficulty_parameters=difficulty_parameters,
        ),
        physiology=PhysiologySpec(
            engine_version=PHYSIO_ENGINE_VERSION,
            initial_vitals=initial_vitals,
        ),
        interaction=InteractionSpec(
            allowed_disclosures=allowed,
            hidden_facts=hidden_facts,
            family_personas=family_personas,
        ),
        grading_blueprint=None,  # authored separately; see cases/rubrics/
        provenance=Provenance(
            author=str(author),
            clinical_reviewer=reviewer,
            sources=sources,
            guideline_versions=guidelines,
            reviewed_at=reviewed_at,
            content_hash="",
        ),
    ).with_content_hash()

    return CaseMigrationResult(
        case_id=case_id,
        tier=tier,
        converted=True,
        report=report,
        missing_required=missing,
        warnings=warnings,
        blueprint=blueprint,
    )


# ── corpus driver ─────────────────────────────────────────────────────────────

def load_corpus(path: Path) -> list[dict]:
    """Accept a directory of case files or a single JSON list/object."""
    if path.is_dir():
        cases: list[dict] = []
        for f in sorted(path.glob("*.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            cases.extend(payload if isinstance(payload, list) else [payload])
        return cases
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else [payload]


def run_migration(
    corpus_path: Path,
    known_action_ids: frozenset[str] | set[str] = KNOWN_ACTION_IDS,
) -> CorpusMigrationReport:
    report = CorpusMigrationReport()
    for index, v1 in enumerate(load_corpus(corpus_path)):
        v1 = dict(v1)
        # The seed corpus omits case_id; the sampler stamps it at sample time.
        v1.setdefault("case_id", v1.get("case_id") or f"SEED-{index:03d}")
        report.add(migrate_case_v1_to_v2(v1, known_action_ids))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate v1 cases to CaseBlueprintV2")
    parser.add_argument("corpus", type=Path, help="directory of *.json cases, or a JSON file")
    parser.add_argument("--out", type=Path, default=None, help="write machine-readable report here")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero if any case failed to convert",
    )
    args = parser.parse_args(argv)

    if not args.corpus.exists():
        print(f"corpus path not found: {args.corpus}", file=sys.stderr)
        return 2

    report = run_migration(args.corpus)
    print(report.render())

    out = args.out or args.corpus.parent / "migration_report.json"
    out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"\nmachine-readable report: {out}")

    if args.strict and report.converted != report.examined:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
