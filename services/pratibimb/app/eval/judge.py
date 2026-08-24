from __future__ import annotations

import json
import os

from openai import AsyncOpenAI

from shared.schemas.case import CaseBlueprint


class LLMJudge:
    """LLM-as-judge for communication and ethical dimensions."""

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.client = AsyncOpenAI(
            base_url=base_url or os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
            api_key=os.getenv("LLM_API_KEY", "sk-local"),
        )
        self.model = model or os.getenv("JUDGE_MODEL", os.getenv("LLM_MODEL", "llama3.1:8b"))

    async def score_communication(
        self,
        case: CaseBlueprint,
        transcript: list[dict],
    ) -> tuple[float, float, list[str]]:
        """
        Returns (communication_score, ethical_score, notes).
        Falls back to heuristic if LLM unavailable.
        """
        prompt = f"""Score this clinical simulation transcript on communication (0-10) and ethical/legal (0-10).

Patient: {case.demographics.name}, case: {case.hidden.primary_diagnosis}
Expected empathy: greet in native language, explain clearly, obtain consent.

Transcript:
{json.dumps(transcript, indent=2)}

Respond JSON: {{"communication": <0-10>, "ethical_legal": <0-10>, "notes": ["..."]}}
"""
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return (
                float(data.get("communication", 5.0)),
                float(data.get("ethical_legal", 5.0)),
                data.get("notes", []),
            )
        except Exception:
            return self._heuristic_communication(transcript)

    def _heuristic_communication(self, transcript: list[dict]) -> tuple[float, float, list[str]]:
        student_turns = [t for t in transcript if t.get("role") == "student"]
        if not student_turns:
            return 3.0, 5.0, ["No student dialogue recorded"]

        avg_len = sum(len(t.get("content", "")) for t in student_turns) / len(student_turns)
        comm = min(10.0, 4.0 + avg_len / 30)
        ethical = 6.0 if any("consent" in t.get("content", "").lower() for t in student_turns) else 5.0
        return comm, ethical, ["Heuristic scoring — LLM judge unavailable"]
