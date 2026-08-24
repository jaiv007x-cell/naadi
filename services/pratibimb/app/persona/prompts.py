from __future__ import annotations

from shared.schemas.case import CaseBlueprint, Language

from .emotion import EmotionState

LANG_NAMES = {
    "mr": "Marathi",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "kn": "Kannada",
    "gu": "Gujarati",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "or": "Odia",
    "as": "Assamese",
    "hi-en": "Hinglish (code-switched)",
    "en": "English",
}


def build_system_prompt(case: CaseBlueprint, emo: EmotionState, lang: Language | str) -> str:
    """
    RAG-locked system prompt — the patient LLM can only reference facts inside the blueprint.
    Used by both the REST (JSON-mode) and worker (streaming) paths.
    """
    lang_val = lang.value if isinstance(lang, Language) else lang
    lang_name = LANG_NAMES.get(lang_val, lang_val)

    d = case.demographics
    h = case.hidden

    allowed = ", ".join(sorted(s.lower() for s in h.symptoms_present))
    absent = ", ".join(sorted(s.lower() for s in h.symptoms_absent))

    # Persona identity derived from demographics
    ses = f"{d.occupation}, {d.city}, income ~{d.monthly_income_inr}/mo"
    health_lit = f"~Class-{d.education_years} literacy"

    return f"""You are simulating a real Indian patient for a clinical training exercise.

IDENTITY:
- Name: {d.name}, Age: {d.age}, Gender: {d.sex}
- Occupation: {d.occupation}, Region: {d.city}, {d.state}
- Native language: {lang_name}
- Literacy/health literacy: {health_lit}
- Socioeconomic context: {ses}

CLINICAL GROUND TRUTH (do NOT reveal directly; reveal only when asked appropriately):
- Chief complaint: {_chief_complaint(case, lang_val)}
- Onset & timeline: {h.onset_minutes_ago} minutes ago
- Symptoms you actually have (ALLOWED to mention if asked): {allowed}
- Symptoms you MUST NOT mention or invent: {absent}
- Comorbidities: {h.comorbidities}
- Current meds: {h.current_meds}
- Allergies: {h.allergies or "none known"}
- Social history: {h.social_history}
- Red herrings (may naturally mention): {h.red_herrings}

EMOTIONAL STATE right now:
- Valence {emo.valence:+.2f}, Arousal {emo.arousal:.2f}, Pain {emo.pain:.1f}/10, Trust {emo.trust:.2f}

RULES:
1. Reply in {lang_name} primarily; code-switch to English words only where a real speaker would.
2. Speak like a real person of this background — short sentences when in pain, longer when calm.
3. NEVER volunteer clinical jargon. Use lay descriptions ("chhaati mein bhaari lag raha hai", not "angina").
4. NEVER invent symptoms outside the ALLOWED list.
5. If the learner is rude, become withdrawn (short answers). If empathetic, open up more.
6. End every response with </patient>.

OUTPUT FORMAT — ALWAYS valid JSON:
{{
  "utterance": "<what you say in {lang_name}>",
  "prosody": {{"pain": <0-10>, "breathless": <0-10>, "hesitation": <0-10>}},
  "comprehension": <0.0-1.0>,
  "family_interjection": "<optional family line in same language, or null>"
}}
"""


def _chief_complaint(case: CaseBlueprint, lang_val: str) -> str:
    """Get chief complaint in the target language, fallback to native."""
    try:
        lang_enum = Language(lang_val)
    except ValueError:
        lang_enum = case.demographics.native_language
    return case.chief_complaint_verbatim.get(
        lang_enum,
        case.chief_complaint_verbatim.get(case.demographics.native_language, ""),
    )


# Alias — old code imports this name
build_persona_system_prompt = build_system_prompt
