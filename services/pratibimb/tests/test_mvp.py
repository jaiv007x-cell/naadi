from __future__ import annotations

import pytest

from services.pratibimb.app.case_gen.sampler import CaseSampler
from services.pratibimb.app.physio.state import PhysiologyEngine
from shared.schemas.case import Language

from services.pratibimb.app.eval.rubric import Competency


def test_sampler_returns_case():
    sampler = CaseSampler()
    case = sampler.sample(target_difficulty=0.55, state="Maharashtra")
    assert case.case_id.startswith("PRB-")
    assert case.demographics.name == "Ramesh Kale"
    assert case.hidden.primary_diagnosis == "Inferior wall STEMI"


def test_sampler_respects_state_filter():
    sampler = CaseSampler()
    case = sampler.sample(state="Karnataka")
    assert case.demographics is not None


def test_physio_engine_baseline():
    sampler = CaseSampler()
    case = sampler.sample()
    engine = PhysiologyEngine(case)
    assert engine.state.vitals.hr == case.baseline_vitals.hr


def test_physio_aspirin_action():
    sampler = CaseSampler()
    case = sampler.sample()
    engine = PhysiologyEngine(case)
    result = engine.apply_action("give_aspirin_325_chewed")
    assert "aspirin" in result["observed"].lower()


def test_physio_ecg_action():
    sampler = CaseSampler()
    case = sampler.sample()
    engine = PhysiologyEngine(case)
    result = engine.apply_action("order_ecg_within_10min")
    assert "ST elevation" in result["findings"]


def test_chief_complaint_languages():
    sampler = CaseSampler()
    case = sampler.sample()
    assert Language.MARATHI in case.chief_complaint_verbatim
    assert Language.HINDI in case.chief_complaint_verbatim


@pytest.mark.asyncio
async def test_scorer_deterministic():
    from services.pratibimb.app.eval.scorer import Scorer, score_session, Turn, build_turns
    from services.pratibimb.app.physio.state import PhysioTrace

    sampler = CaseSampler()
    case = sampler.sample()
    scorer = Scorer()

    action_log = [
        {"action": "order_ecg_within_10min", "t": 120},
        {"action": "give_aspirin_325_chewed", "t": 180},
        {"action": "arrange_pci_transfer", "t": 400},
    ]
    transcript = [
        {"role": "student", "content": "Namaste, kasa aahet tumhi?", "lang": "mr"},
        {"role": "patient", "content": "Doctor, chatit jad watatay", "lang": "mr"},
    ]

    rubric = await scorer.score(case, action_log, transcript, stress_delta=0.5)
    assert rubric.clinical_reasoning >= 0.0
    assert rubric.overall > 0

    turns = build_turns(action_log, transcript, case.patient_lang)
    card = score_session(case, turns, PhysioTrace())
    assert card.total > 0
    assert Competency.CLINICAL_JUDGMENT.value in card.competency_scores
    assert Competency.PHARMACOLOGICAL.value in card.competency_scores
