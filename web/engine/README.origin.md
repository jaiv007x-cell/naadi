# NAADI — Pratibimb Clinical Twin

**Smallest thing that proves the loop works end-to-end:**

```
case generation → patient dialogue → physiological state → evaluation → Dhaara update
```

Runs locally first. Cloud-optional. Vernacular ASR/TTS wired behind interfaces (swap Bhashini/IndicWhisper when keys are ready).

## Quick Start

```bash
# Install
cd naadi
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"

# Copy env
copy .env.example .env

# Terminal 1 — Dhaara (competency graph stub)
set PYTHONPATH=.
uvicorn services.dhaara.app.main:app --port 8200 --reload

# Terminal 2 — Pratibimb (clinical twin)
set PYTHONPATH=.
uvicorn services.pratibimb.app.main:app --port 8100 --reload

# Terminal 3 — Gateway (optional)
set PYTHONPATH=.
uvicorn services.gateway.app.main:app --port 8000 --reload

# Run demo session
python scripts/run_demo_session.py
```

Or with Docker:

```bash
docker compose up --build
```

## Architecture

Two paths into the same engine:

1. **REST MVP** — direct HTTP to Pratibimb (`8100`)
2. **WebSocket live** — Gateway (`8000`) → Redis Streams → Pratibimb worker → fanout

```
Client ──REST──▶ Pratibimb ──▶ SessionManager
  │
  └──WS──▶ Gateway ──▶ Redis (Dhaara streams)
                          │
                          ▼
                     Pratibimb worker
                          │
                          ▼
                     Scorecard → Dhaara competency graph
```

### Key modules

| Module | Role |
|--------|------|
| `dhaara_client.py` | Redis Streams event bus |
| `persona/llm.py` | vLLM streaming (OpenAI-compatible) |
| `speech/asr.py` | faster-whisper code-switch ASR |
| `speech/tts.py` | Coqui XTTS-v2 vernacular TTS |
| `eval/scorer.py` | Full rubric + hallucination check |
| `worker.py` | Consumes gateway events from Redis |

### vLLM (optional GPU profile)

```bash
docker compose --profile gpu up vllm
# or point VLLM_URL at Ollama: http://localhost:11434/v1
```

### Speech (optional)

```bash
pip install -e ".[speech]"
SPEECH_MODE=whisper   # faster-whisper ASR
SPEECH_MODE=xtts      # Coqui XTTS (needs TTS_SPEAKER_WAV)
```

## API (Pratibimb)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/sessions` | Create session from VCC |
| POST | `/v1/sessions/{id}/start` | Begin simulation |
| POST | `/v1/sessions/{id}/dialogue` | Student speaks → patient responds |
| POST | `/v1/sessions/{id}/actions` | Clinical action (ECG, aspirin, etc.) |
| POST | `/v1/sessions/{id}/complete` | Score session, push to Dhaara |
| GET | `/v1/sessions/{id}/events` | Event log |

## LLM Setup

Point at any OpenAI-compatible endpoint (Ollama, vLLM):

```env
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1:8b
```

Without an LLM, the persona falls back to deterministic stub responses from the seed case.

## Speech (Stub → Production)

```env
SPEECH_MODE=stub          # default — no audio
SPEECH_MODE=bhashini      # TTS via Bhashini (set BHASHINI_API_KEY)
SPEECH_MODE=indicwhisper  # ASR via IndicWhisper (set INDICWHISPER_API_KEY)
```

## Tests

```bash
set PYTHONPATH=.
pytest services/pratibimb/tests -v
```

## Seed Case

Ramesh Kale, 47M, auto-rickshaw driver from Nagpur — **Inferior wall STEMI**.

Critical actions: ECG within 10 min, aspirin 325mg chewed, PCI transfer.
