from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from shared.schemas.case import Language


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str
    lang: str
    avg_logprob: float


class ASRProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, language: Language) -> str:
        ...

    def transcribe_segments(
        self, pcm16: bytes, sample_rate: int = 16000, hint_langs: Iterable[str] | None = None
    ) -> list[ASRSegment]:
        raise NotImplementedError


class StubASR(ASRProvider):
    async def transcribe(self, audio_bytes: bytes, language: Language) -> str:
        return "[ASR stub — audio received, set SPEECH_MODE=whisper to enable]"


class CodeSwitchASR(ASRProvider):
    """
    Whisper-large-v3 via faster-whisper with code-switch rescoring.
    Requires optional deps: pip install naadi[speech]
    """

    def __init__(self) -> None:
        import numpy as np
        from faster_whisper import WhisperModel

        model_size = os.getenv("ASR_MODEL", "large-v3")
        device = os.getenv("ASR_DEVICE", "cpu")
        compute = os.getenv("ASR_COMPUTE", "int8")
        self._model = WhisperModel(model_size, device=device, compute_type=compute)
        self._np = np

    def transcribe_segments(
        self,
        pcm16: bytes,
        sample_rate: int = 16000,
        hint_langs: Iterable[str] = ("hi", "en", "ta", "te", "bn", "mr"),
    ) -> list[ASRSegment]:
        audio = self._np.frombuffer(pcm16, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        segments, info = self._model.transcribe(
            audio,
            vad_filter=True,
            beam_size=5,
            language=None,
            condition_on_previous_text=False,
            task="transcribe",
        )
        out: list[ASRSegment] = []
        for s in segments:
            lang = info.language or "hi"
            if s.avg_logprob < -1.0:
                best = None
                for lg in hint_langs:
                    seg2, _ = self._model.transcribe(
                        audio[int(s.start * sample_rate) : int(s.end * sample_rate)],
                        language=lg,
                        beam_size=3,
                        vad_filter=False,
                    )
                    seg2 = list(seg2)
                    if seg2 and (best is None or seg2[0].avg_logprob > best[1]):
                        best = (lg, seg2[0].avg_logprob, seg2[0].text)
                if best:
                    out.append(ASRSegment(s.start, s.end, best[2].strip(), best[0], best[1]))
                    continue
            out.append(ASRSegment(s.start, s.end, s.text.strip(), lang, s.avg_logprob))
        return out

    async def transcribe(self, audio_bytes: bytes, language: Language) -> str:
        segs = self.transcribe_segments(audio_bytes)
        return " ".join(s.text for s in segs) if segs else ""


class IndicWhisperASR(StubASR):
    """Legacy alias — routes to CodeSwitchASR when available."""

    pass


def get_asr_provider(mode: str | None = None) -> ASRProvider:
    mode = mode or os.getenv("SPEECH_MODE", "stub")
    if mode in ("whisper", "indicwhisper"):
        try:
            return CodeSwitchASR()
        except ImportError:
            return StubASR()
    return StubASR()
