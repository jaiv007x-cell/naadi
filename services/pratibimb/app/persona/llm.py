from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from shared.schemas.case import CaseBlueprint, Language

from .emotion import EmotionState
from .prompts import build_persona_system_prompt, build_system_prompt


VLLM_URL = os.getenv("VLLM_URL", os.getenv("LLM_BASE_URL", "http://localhost:8000/v1"))
VLLM_MODEL = os.getenv("VLLM_MODEL", os.getenv("LLM_MODEL", "pratibimb/patient-llama3.1-8b-lora"))
API_KEY = os.getenv("VLLM_API_KEY", os.getenv("LLM_API_KEY", "EMPTY"))


class PersonaLLM:
    """
    vLLM OpenAI-compatible endpoint for fine-tuned patient persona.
    Falls back to deterministic stub when vLLM is unavailable.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        url = (base_url or VLLM_URL).rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1" if not url.endswith("/v1/") else url.rstrip("/")
        self._base_url = url
        self._model = model or VLLM_MODEL
        self._client = httpx.AsyncClient(
            timeout=60.0,
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

    async def reply(
        self,
        case: CaseBlueprint,
        emotion: EmotionState,
        history: list[dict],
        user_utterance: str,
        lang: str | Language,
    ) -> AsyncIterator[str]:
        lang_val = lang.value if isinstance(lang, Language) else lang
        lang_enum = Language(lang_val) if lang_val in {l.value for l in Language} else Language.HINDI
        system = build_system_prompt(case, emotion, lang_enum)
        messages = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user_utterance},
        ]
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.6,
            "top_p": 0.9,
            "max_tokens": 256,
            "stream": True,
            "stop": ["</patient>"],
        }
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
                    except Exception:
                        continue
        except Exception:
            fallback = self._fallback_response(case, emotion, lang_enum)
            yield json.dumps(fallback)

    async def respond(
        self,
        case: CaseBlueprint,
        emotion: EmotionState,
        history: list[dict],
        student_utterance: str,
        language: Language,
    ) -> dict:
        """Collect streamed reply and parse JSON persona output."""
        chunks: list[str] = []
        async for delta in self.reply(case, emotion, history, student_utterance, language):
            chunks.append(delta)
        raw = "".join(chunks).strip()
        if not raw:
            return self._fallback_response(case, emotion, language)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Non-JSON stream — wrap as utterance
            if raw.startswith("{"):
                try:
                    return json.loads(raw.split("}", 1)[0] + "}")
                except json.JSONDecodeError:
                    pass
            return {
                "utterance": raw,
                "prosody": {"pain": emotion.pain, "breathless": emotion.anxiety * 0.8, "hesitation": 3},
                "comprehension": emotion.comprehension,
                "family_interjection": None,
            }

    def _fallback_response(
        self, case: CaseBlueprint, emotion: EmotionState, language: Language
    ) -> dict:
        complaint = case.chief_complaint_verbatim.get(
            language,
            case.chief_complaint_verbatim.get(case.demographics.native_language, ""),
        )
        family = None
        if case.family_present:
            family = (
                "Doctor, please help him quickly!"
                if language == Language.ENGLISH
                else "Doctor, lavkar bagha!"
            )
        return {
            "utterance": complaint,
            "prosody": {
                "pain": emotion.pain,
                "breathless": min(10, emotion.anxiety * 0.8),
                "hesitation": emotion.anxiety * 0.5,
            },
            "comprehension": emotion.comprehension,
            "family_interjection": family,
        }

    async def aclose(self) -> None:
        await self._client.aclose()
