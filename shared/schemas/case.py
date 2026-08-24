from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Language(str, Enum):
    HINDI = "hi"
    MARATHI = "mr"
    TAMIL = "ta"
    TELUGU = "te"
    BENGALI = "bn"
    KANNADA = "kn"
    GUJARATI = "gu"
    MALAYALAM = "ml"
    PUNJABI = "pa"
    ODIA = "or"
    ASSAMESE = "as"
    HINGLISH = "hi-en"
    ENGLISH = "en"


class Demographics(BaseModel):
    name: str
    age: int
    sex: Literal["M", "F", "O"]
    occupation: str
    state: str
    city: str
    native_language: Language
    education_years: int
    monthly_income_inr: int


class Vital(BaseModel):
    hr: float
    sbp: float
    dbp: float
    spo2: float
    rr: float
    temp_c: float


class HiddenTruth(BaseModel):
    """Ground truth the student must uncover. Persona LLM sees this but is RAG-locked to it."""

    primary_diagnosis: str
    icd10: str
    onset_minutes_ago: int
    symptoms_present: list[str]
    symptoms_absent: list[str]
    comorbidities: list[str]
    current_meds: list[str]
    allergies: list[str]
    social_history: dict
    red_herrings: list[str] = Field(default_factory=list)


class PatientPhysiology(BaseModel):
    """Patient-level physiology context for PK/PD scaling."""

    weight_kg: float = 70.0
    height_cm: float = 170.0
    hepatic_function: float = Field(1.0, ge=0.0, le=1.5)
    renal_function: float = Field(1.0, ge=0.0, le=1.5)
    pregnant: bool = False
    gestational_weeks: int | None = None


class CaseBlueprint(BaseModel):
    case_id: str
    difficulty: float = Field(ge=0.0, le=1.0)
    demographics: Demographics
    chief_complaint_verbatim: dict[Language, str]
    baseline_vitals: Vital
    hidden: HiddenTruth
    expected_actions: list[str]
    critical_actions: list[str]
    time_pressure_seconds: int
    family_present: bool
    resources_available: list[str]
    competency_tags: list[str] = Field(default_factory=list)
    red_flag: str | None = None
    symptom_vocab: list[str] = Field(default_factory=list)
    initial_pain: float = 5.0
    persona_voice_id: str = "default_male_mr"
    patient_physiology: PatientPhysiology = Field(default_factory=PatientPhysiology)
    probe_only: bool = False

    @property
    def patient_lang(self) -> str:
        return self.demographics.native_language.value

    @property
    def expected_orders(self) -> set[str]:
        mapping = {
            "order_ecg_within_10min": "ECG",
            "order_ecg": "ECG",
            "order_troponin": "troponin",
            "give_aspirin_325_chewed": "aspirin_325",
            "arrange_pci_transfer": "pci_transfer",
            "give_atorvastatin_80": "atorvastatin_80",
        }
        return {mapping[a] for a in self.expected_actions if a in mapping}

    @property
    def allowed_symptoms(self) -> set[str]:
        return {s.lower() for s in self.hidden.symptoms_present}

    @property
    def expected_procedure_sequence(self) -> list[str]:
        action_map = {
            "greet_patient_in_native_language": "greet",
            "take_focused_history": "history",
            "measure_vitals": "vitals",
            "order_ecg_within_10min": "order:ECG",
            "order_ecg": "order:ECG",
            "order_troponin": "order:troponin",
            "give_aspirin_325_chewed": "give:aspirin_325",
            "give_atorvastatin_80": "give:atorvastatin_80",
            "obtain_consent_transfer": "consent",
            "arrange_pci_transfer": "transfer:pci",
        }
        return [action_map[a] for a in self.expected_actions if a in action_map]

    def resolved_red_flag(self) -> str | None:
        if self.red_flag:
            return self.red_flag
        dx = self.hidden.primary_diagnosis.lower()
        if "stemi" in dx or "mi" in dx or "infarction" in dx:
            return "MI"
        if "sepsis" in dx:
            return "sepsis"
        if "stroke" in dx:
            return "stroke"
        return None

    def resolved_symptom_vocab(self) -> set[str]:
        if self.symptom_vocab:
            return {s.lower() for s in self.symptom_vocab}
        vocab: set[str] = set()
        for s in self.hidden.symptoms_present + self.hidden.symptoms_absent:
            for tok in s.lower().replace("-", " ").split():
                if len(tok) >= 4:
                    vocab.add(tok)
        vocab.update(["fever", "cough", "paralysis", "seizure", "hematemesis", "syncope"])
        return vocab
