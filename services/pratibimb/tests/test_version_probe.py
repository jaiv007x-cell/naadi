"""Tests for the physiology version probe — behavioral hashing."""

from __future__ import annotations

from unittest.mock import patch

from services.pratibimb.app.physio.version_probe import (
    PROBE_SUITE,
    PROBE_SUITE_VERSION,
    PhysiologyManifest,
    compute_manifest,
)
from services.pratibimb.app.case_gen.sampler import CaseSampler


def test_manifest_is_stable_across_calls():
    a = compute_manifest()
    b = compute_manifest()
    assert a.behavior_hash == b.behavior_hash
    assert a.per_trajectory_hashes == b.per_trajectory_hashes


def test_manifest_shape():
    m = compute_manifest()
    assert isinstance(m, PhysiologyManifest)
    assert m.version.startswith("physio@")
    assert len(m.behavior_hash) == 64
    assert len(m.per_trajectory_hashes) == len(PROBE_SUITE)
    for name, h in m.per_trajectory_hashes:
        assert isinstance(name, str) and len(name) > 0
        assert len(h) == 64


def test_manifest_changes_when_engine_drifts():
    """Monkeypatch a PD constant on morphine and verify the hash changes."""
    from services.pratibimb.app.physio.pharmacology import DRUG_REGISTRY

    baseline = compute_manifest()

    morphine = DRUG_REGISTRY["morphine"]
    pd0 = morphine.pd[0]
    original_emax = pd0.emax
    try:
        object.__setattr__(pd0, "emax", original_emax * 5.0)
        drifted = compute_manifest()
    finally:
        object.__setattr__(pd0, "emax", original_emax)

    assert baseline.behavior_hash != drifted.behavior_hash


def test_per_trajectory_localization():
    """Only the morphine trajectory should change when morphine PD shifts."""
    from services.pratibimb.app.physio.pharmacology import DRUG_REGISTRY

    baseline = compute_manifest()

    morphine = DRUG_REGISTRY["morphine"]
    pd0 = morphine.pd[0]
    original_emax = pd0.emax
    try:
        object.__setattr__(pd0, "emax", original_emax * 5.0)
        drifted = compute_manifest()
    finally:
        object.__setattr__(pd0, "emax", original_emax)

    base_map = dict(baseline.per_trajectory_hashes)
    drift_map = dict(drifted.per_trajectory_hashes)

    assert base_map["stemi_untreated_deterioration"] == drift_map["stemi_untreated_deterioration"]
    assert base_map["stemi_aspirin_only"] == drift_map["stemi_aspirin_only"]
    assert base_map["stemi_aspirin_then_morphine"] != drift_map["stemi_aspirin_then_morphine"]


def test_probe_suite_version_pinned():
    assert PROBE_SUITE_VERSION == "1.0.0"


def test_probe_case_is_not_sampleable():
    sampler = CaseSampler()
    ids = {sampler.sample().case_id for _ in range(200)}
    assert not any(cid.startswith("probe.") for cid in ids)


def test_to_ledger_dict_roundtrip():
    m = compute_manifest()
    d = m.to_ledger_dict()
    assert d["behavior_hash"] == m.behavior_hash
    assert d["probe_suite_version"] == PROBE_SUITE_VERSION
    assert len(d["per_trajectory_hashes"]) == len(PROBE_SUITE)
