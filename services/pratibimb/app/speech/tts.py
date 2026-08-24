from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod

from shared.schemas.case import Language


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, language: Language, speaker_wav: str | None = None) -> bytes | None:
        ...


class StubTTS(TTSProvider):
    async def synthesize(self, text: str, language: Language, speaker_wav: str | None = None) -> bytes | None:
        return None


class VernacularTTS(TTSProvider):
    """
    Coqui XTTS-v2 for multilingual patient voice.
    Requires optional deps: pip install naadi[speech]
    """

    def __init__(self) -> None:
        import soundfile as sf
        from TTS.api import TTS

        model = os.getenv("TTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")
        device = os.getenv("TTS_DEVICE", "cpu")
        self._tts = TTS(model).to(device)
        self._sf = sf

    def synth(self, text: str, lang: str, speaker_wav: str) -> bytes:
        wav = self._tts.tts(text=text, language=lang, speaker_wav=speaker_wav)
        buf = io.BytesIO()
        self._sf.write(buf, wav, 24000, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def synthesize(self, text: str, language: Language, speaker_wav: str | None = None) -> bytes | None:
        ref = speaker_wav or os.getenv("TTS_SPEAKER_WAV", "")
        if not ref:
            return None
        return self.synth(text, language.value, ref)


class BhashiniTTS(StubTTS):
    """Legacy alias — use VernacularTTS locally or wire Bhashini API when keys ready."""

    pass


def get_tts_provider(mode: str | None = None) -> TTSProvider:
    mode = mode or os.getenv("SPEECH_MODE", "stub")
    if mode in ("xtts", "coqui"):
        try:
            return VernacularTTS()
        except ImportError:
            return StubTTS()
    if mode == "bhashini":
        api_key = os.getenv("BHASHINI_API_KEY", "")
        if not api_key:
            return StubTTS()
        # TODO: wire Bhashini HTTP pipeline
        return StubTTS()
    return StubTTS()
