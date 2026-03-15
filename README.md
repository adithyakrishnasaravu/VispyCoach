# VispyCoach

Real-time AI coach for sales reps on live calls.

VispyCoach listens to your customer, detects rising frustration and objection signals, and whispers actionable tips into the rep's earpiece — before the deal slips. When a call goes critical, it instantly alerts management with the live transcript and emotion analysis, so a manager can send strategy directly to the rep mid-call without interrupting.

---

## How it works

```
Customer voice → Pulse STT → Emotion analysis → Groq coaching tip → Lightning TTS → rep's earpiece
                                      ↓
                           Objection / stress spike
                                      ↓
                         Management dashboard alert
                                      ↓
                        Manager sends strategy → rep's screen
```

**Full pipeline latency: under 2 seconds.**

---

## Features

- **Live transcription** — Smallest AI Pulse STT streams the customer's words in real time
- **Emotion tracking** — anger, fear, sadness, disgust scored per utterance with a rolling stress meter
- **Autonomous coaching** — Groq fires a one-sentence tip into the rep's earpiece the moment stress spikes, no button press needed
- **Ask Coach (V key)** — rep holds V, asks a question out loud, gets a context-aware answer using the full call transcript
- **Management escalation** — objection keywords or high stress automatically sends a live alert to the management dashboard
- **Manager reply** — manager types strategy, rep sees it as a banner mid-call without any interruption

---

## Stack

| Layer | Tech |
|-------|------|
| STT | Smallest AI Pulse (WebSocket streaming) |
| TTS | Smallest AI Lightning v3.1 |
| LLM | Groq — llama-3.1-8b-instant |
| Backend | FastAPI + WebSockets |
| Frontend | Vanilla JS, Web Speech API |

---

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Add API keys
cp .env.example .env
# Fill in SMALLEST_API_KEY and GROQ_API_KEY

# Run
uvicorn server:app --host 0.0.0.0 --port 8765 --reload
```

Open:
- Rep UI → `http://localhost:8765`
- Management dashboard → `http://localhost:8765/management`

---

## Usage

1. Paste lead/customer context into the rep UI
2. Click **Start Listening** — mic captures call audio
3. Speak or play the customer's voice through your speakers
4. Coaching tips fire automatically when stress is detected
5. Hold **V** to ask the coach a question mid-call
6. The Management tab receives escalation alerts in real time
7. Press **/** on the management dashboard to type a reply → **Enter** to send

---

## Environment variables

```
SMALLEST_API_KEY=your_smallest_ai_key
GROQ_API_KEY=your_groq_key
```

---
