"""
VispyCoach Web Server
=======================
FastAPI + WebSocket backend. Browser streams audio → Pulse STT → Groq tip → Lightning TTS → browser earpiece.

Run:  uvicorn server:app --host 0.0.0.0 --port 8765 --reload
Open: http://localhost:8765
"""

import asyncio
import base64
import json
import os
import re
import time
from collections import deque

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(override=True)

SMALLEST_API_KEY = os.getenv("SMALLEST_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

STT_WS_URL = "wss://api.smallest.ai/waves/v1/pulse/get_text"
TTS_URL = "https://api.smallest.ai/waves/v1/lightning-v3.1/get_speech"

app = FastAPI()

# ── Serve static files (index.html) ──────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Global management state ───────────────────────────────────────────────────
management_connections: list = []   # active management WebSocket connections
active_rep: dict = {
    "ws": None,
    "lead_info": "",
    "transcript": deque(maxlen=50),
}


@app.get("/")
async def root():
    with open("static/index.html") as f:
        return HTMLResponse(f.read())


@app.get("/management")
async def management_page():
    with open("static/management.html") as f:
        return HTMLResponse(f.read())


async def broadcast_to_management(payload: dict):
    dead = []
    for mgmt_ws in management_connections:
        try:
            await mgmt_ws.send_text(json.dumps(payload))
        except Exception:
            dead.append(mgmt_ws)
    for d in dead:
        management_connections.remove(d)


@app.post("/inject-transcript")
async def inject_transcript(request: Request):
    payload = await request.json()
    """Dev endpoint: inject a text line directly into the emotion/escalation pipeline."""
    text = payload.get("text", "").strip() if payload else ""
    if not text:
        return {"status": "empty"}
    ws = active_rep["ws"]
    if ws is None:
        return {"status": "no_rep_connected"}

    emotions, stress = analyze_text(text)
    active_rep["transcript"].append(text)

    await ws.send_text(json.dumps({"type": "transcript", "text": text, "is_final": True}))
    await ws.send_text(json.dumps({"type": "emotion", "emotions": emotions, "stress": int(min(100, stress * 15))}))

    objection_patterns = [
        r"\b(not interested|no longer interested)\b",
        r"\b(too expensive|can.t afford|no budget|out of budget)\b",
        r"\b(going with|switching to|already using|chose another)\b",
        r"\b(cancel|cancelling|canceling)\b",
        r"\b(not the right fit|doesn.t work for us|not a good fit)\b",
        r"\b(competitor|alternative|other (vendor|option|solution))\b",
        r"\b(lawsuit|lawyer|sue|legal action)\b",
    ]
    objection_hit = next((p for p in objection_patterns if re.search(p, text, re.IGNORECASE)), None)
    dominant = max(emotions, key=lambda k: emotions[k])
    should_escalate = bool(management_connections and (objection_hit or stress >= 2.0))

    if should_escalate:
        snippet = list(active_rep["transcript"])[-5:]
        await broadcast_to_management({
            "type": "escalation",
            "reason": "objection detected" if objection_hit else "high stress",
            "trigger_text": text,
            "emotion": dominant,
            "stress_pct": int(min(100, stress * 15)),
            "transcript": snippet,
            "lead_info": active_rep["lead_info"],
            "ts": time.strftime("%H:%M:%S"),
        })

    return {"status": "ok", "stress": stress, "emotion": dominant, "escalated": should_escalate}


@app.get("/test-escalation")
async def test_escalation():
    """Dev endpoint: fire a fake escalation to all open management dashboards."""
    payload = {
        "type": "escalation",
        "reason": "objection detected",
        "trigger_text": "This is a test escalation — I'm not interested anymore, it's too expensive.",
        "emotion": "anger",
        "stress_pct": 72,
        "transcript": [
            "Rep: Can I tell you about our pricing?",
            "Customer: I've heard this before.",
            "Rep: We have flexible plans.",
            "Customer: Look, I'm not interested anymore, it's too expensive.",
        ],
        "lead_info": "Test lead — ACME Corp, VP of Sales",
        "ts": time.strftime("%H:%M:%S"),
    }
    await broadcast_to_management(payload)
    return {"status": "sent", "to": len(management_connections)}


@app.websocket("/ws/management")
async def management_ws(ws: WebSocket):
    await ws.accept()
    management_connections.append(ws)
    print(f"[mgmt] dashboard connected ({len(management_connections)} total)")
    try:
        async for raw in ws.iter_text():
            data = json.loads(raw)
            if data.get("type") == "manager_message" and active_rep["ws"]:
                text = data.get("text", "").strip()
                if text:
                    print(f"[mgmt] → rep: {text}")
                    await active_rep["ws"].send_text(json.dumps({
                        "type": "manager_message",
                        "text": text,
                    }))
    except WebSocketDisconnect:
        pass
    finally:
        if ws in management_connections:
            management_connections.remove(ws)
        print(f"[mgmt] dashboard disconnected ({len(management_connections)} remaining)")


# ── Emotion lexicon (same as emotion_agent.py) ───────────────────────────────
EMOTION_LEXICON = {
    "furious": ("anger", 3), "outraged": ("anger", 3), "livid": ("anger", 3),
    "infuriated": ("anger", 3), "disgusting": ("disgust", 3), "unacceptable": ("anger", 3),
    "appalling": ("anger", 3), "ridiculous": ("anger", 2.5), "absurd": ("anger", 2.5),
    "angry": ("anger", 2), "frustrated": ("anger", 2), "annoyed": ("anger", 2),
    "terrible": ("anger", 2), "awful": ("anger", 2), "horrible": ("anger", 2),
    "hate": ("anger", 2), "useless": ("anger", 2), "incompetent": ("anger", 2),
    "pathetic": ("anger", 2), "waste": ("anger", 1.5), "garbage": ("anger", 2),
    "refund": ("anger", 2), "cancel": ("anger", 1.5), "lawsuit": ("anger", 3),
    "lawyer": ("anger", 3), "sue": ("anger", 3), "report": ("anger", 1.5),
    "complaint": ("anger", 1.5), "escalate": ("anger", 1.5), "supervisor": ("anger", 1.5),
    "manager": ("anger", 1.5), "again": ("anger", 1), "third": ("anger", 1.5),
    "fourth": ("anger", 1.5), "times": ("anger", 0.5), "weeks": ("anger", 0.5),
    "never": ("anger", 1), "still": ("anger", 1), "waiting": ("anger", 1),
    "nobody": ("anger", 1.5), "ignored": ("anger", 1.5),
    "worried": ("fear", 1.5), "scared": ("fear", 2), "concerned": ("fear", 1),
    "disappointed": ("sadness", 1.5), "devastated": ("sadness", 2), "upset": ("sadness", 1),
}
EMOTIONS = ["anger", "fear", "disgust", "sadness", "happiness"]

KEYWORD_TIPS = [
    (r"\brefund\b", "anger", "Offer a full refund immediately and apologize for the inconvenience."),
    (r"\brefund\b", None, "Process the refund now and confirm the timeline clearly."),
    (r"\bcancel\b", "anger", "Acknowledge their desire to leave, then offer one concrete reason to stay."),
    (r"\b(lawyer|lawsuit|sue|legal)\b", "anger", "Escalate to your supervisor right now and stay calm."),
    (r"\b(manager|supervisor|escalate)\b", None, "Offer to escalate immediately and set a clear callback time."),
    (r"\b(wait|waiting|weeks|days|months)\b", "anger", "Apologize for the delay and give them a specific resolution date."),
    (r"\b(again|third|fourth|multiple)\b", "anger", "Acknowledge they've called before and take personal ownership this time."),
    (r"\b(broken|doesn.t work|not working|useless)\b", None, "Empathize, then offer a concrete fix or replacement option."),
    (r"\b(nobody|no one|ignored)\b", None, "Say: I hear you, and I'm personally going to fix this right now."),
]

EMOTION_TIPS = {
    "anger": [
        "Lower your tone, say their name, and acknowledge their frustration directly.",
        "Say: I completely understand, let me make this right for you right now.",
        "Slow down and offer one concrete next step.",
        "Validate their feelings before jumping to solutions.",
        "Apologize sincerely, then ask: what would make this right for you?",
    ],
    "disgust": [
        "Acknowledge the issue seriously and avoid minimizing their experience.",
        "Say: That should never have happened, and I'm going to fix it now.",
    ],
    "fear": [
        "Reassure them clearly and be specific about next steps.",
        "Say: I understand your concern, here's exactly what will happen next.",
    ],
    "sadness": [
        "Show genuine empathy before moving to any solution.",
        "Say: I'm really sorry this happened, that's completely understandable.",
    ],
}

GROQ_SYSTEM = """You are VispyCoach, a real-time AI coach for sales reps on live calls.
Your tip is SPOKEN ALOUD into their earpiece. Rules:
- ONE sentence, max 12 words.
- Reference something the customer actually said.
- Be specific, calm, actionable. No filler words."""

# ── Emotion agent system prompt ───────────────────────────────────────────────
EMOTION_AGENT_SYSTEM = """You are an emotion analysis engine for live B2B sales calls.

Analyze the customer's emotional state from the full conversation and respond with ONLY a valid JSON object.

Required JSON schema:
{
  "emotions": {
    "anger": <float 0.0-1.0>,
    "fear": <float 0.0-1.0>,
    "sadness": <float 0.0-1.0>,
    "disgust": <float 0.0-1.0>,
    "happiness": <float 0.0-1.0>
  },
  "stress": <integer 0-100>,
  "dominant": <exactly one of: "anger", "fear", "sadness", "disgust", "happiness">,
  "reasoning": <one sentence explaining the emotional state>
}

Guardrails — you MUST follow these:
- All emotion values MUST be floats strictly between 0.0 and 1.0
- stress MUST be an integer between 0 and 100
- dominant MUST be exactly one of the five emotion keys listed above
- reasoning MUST be a single sentence under 30 words
- Base scores on the FULL conversation arc, not just the latest line
- High-risk signals: competitor mentions, cancellation, legal threats, pricing objections, repeated complaints
- Implicit frustration counts: short clipped answers, sarcasm, "fine", "whatever", dismissiveness
- happiness should only be non-zero when the customer is genuinely positive or relieved
- Return ONLY the JSON object, no other text"""

# ── Decision agent system prompt and tools ────────────────────────────────────
DECISION_AGENT_SYSTEM = """You are VispyCoach, an autonomous AI agent monitoring a live B2B sales call in real time.

After each customer utterance you receive emotion scores, stress level, and full transcript context.
You MUST call one or more of the available tools. Calling hold() counts as a valid decision.

Decision rules:
- fire_coaching_tip → stress > 40%, customer shows frustration or objection, rep needs immediate guidance
- escalate_to_management → competitor mentioned, cancellation threatened, legal language (lawsuit/lawyer/sue), stress > 70%, deal at serious risk
- You MAY call BOTH fire_coaching_tip AND escalate_to_management in the same turn
- hold → call is progressing normally, no tension, or insufficient context to act yet

Tip rules:
- Exactly ONE sentence, maximum 12 words
- Must reference something the customer specifically said
- Direct and actionable — no filler, no "remember to", no generic advice
- Bad: "Stay calm and acknowledge their concern"
- Good: "Acknowledge the billing error and offer a same-day refund"

Be selective — do NOT fire tips on every utterance. Only act when it genuinely matters."""

DECISION_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fire_coaching_tip",
            "description": "Send an immediate coaching tip into the rep's earpiece. Use when stress > 40%, customer raises an objection, shows frustration, or the rep needs specific guidance right now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tip": {
                        "type": "string",
                        "description": "One sentence, max 12 words, specific to what the customer just said. No filler.",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "low=awareness tip, medium=act soon, high=act immediately",
                    },
                },
                "required": ["tip", "urgency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_management",
            "description": "Alert management this call needs attention. Use when: competitor mentioned, customer threatens to cancel/leave, legal language appears, stress > 70%, or deal is at serious risk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "One sentence explaining why, referencing what the customer said.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["warning", "critical"],
                        "description": "warning=monitor this call, critical=intervene now",
                    },
                },
                "required": ["reason", "severity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hold",
            "description": "Take no action. Call is proceeding normally, emotions are neutral/positive, no intervention needed.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def analyze_text(text: str):
    words = re.findall(r"\b\w+\b", text.lower())
    scores = {e: 0.0 for e in EMOTIONS}
    for word in words:
        if word in EMOTION_LEXICON:
            emotion, weight = EMOTION_LEXICON[word]
            scores[emotion] += weight
    total_stress = scores["anger"] + scores["disgust"] + scores["fear"]
    display = {e: min(1.0, scores[e] / 5.0) for e in EMOTIONS}
    display["happiness"] = max(0.0, 1.0 - min(1.0, total_stress / 5.0))
    return display, total_stress


def select_template_tip(context: str, emotion: str) -> str:
    import random
    for pattern, tip_emotion, tip in KEYWORD_TIPS:
        if re.search(pattern, context, re.IGNORECASE):
            if tip_emotion is None or tip_emotion == emotion:
                return tip
    tips = EMOTION_TIPS.get(emotion, ["Stay calm, acknowledge their concern, and offer a clear solution."])
    return random.choice(tips)


COACH_QUERY_SYSTEM = """You are an expert sales and support coach assisting a rep on a live call.
You have access to the full call transcript and lead information.
Answer the rep's question concisely and directly — 1-3 sentences max.
Be specific, actionable, and use context from the call."""


async def get_groq_answer(question: str, transcript: list, lead_info: str) -> str:
    if not GROQ_API_KEY:
        return "Groq API key not configured."
    try:
        context = "\n".join(f"- {t}" for t in transcript[-10:]) if transcript else "No transcript yet."
        user_msg = f"Lead info:\n{lead_info or 'None provided'}\n\nCall transcript so far:\n{context}\n\nRep's question: {question}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 120,
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": COACH_QUERY_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[query] error: {e}")
        return "Could not get answer right now."


async def get_groq_tip(context_lines: list, emotion: str) -> str:
    if not GROQ_API_KEY:
        return select_template_tip(" ".join(context_lines), emotion)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 40,
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": GROQ_SYSTEM},
                        {"role": "user", "content": f"Emotion: {emotion}\n\nCustomer said:\n" + "\n".join(f"- {l}" for l in context_lines) + "\n\nCoaching tip:"},
                    ],
                },
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[groq] error: {e}")
        return select_template_tip(" ".join(context_lines), emotion)


async def analyze_emotion_groq(
    latest_text: str,
    transcript: list,
    lead_info: str,
    prev_emotions: dict,
) -> tuple[dict, int, str, str]:
    """
    Groq (JSON mode) analyzes customer emotion from full call context.
    Returns (emotions_dict, stress_pct, dominant, reasoning).
    Falls back to lexicon analyze_text() on any error or timeout.
    """
    VALID_EMOTIONS = {"anger", "fear", "sadness", "disgust", "happiness"}

    def fallback(reason: str):
        # If we have a previous emotion state, preserve it (don't reset to 0 on rate limits)
        if prev_emotions and any(v > 0 for v in prev_emotions.values()):
            dominant = max(prev_emotions, key=lambda k: prev_emotions[k])
            stress_pct = int(min(100, (prev_emotions.get("anger", 0) + prev_emotions.get("disgust", 0) + prev_emotions.get("fear", 0)) * 100))
            print(f"[emotion/groq] fallback ({reason}) → preserved state dominant={dominant} stress={stress_pct}%")
            return dict(prev_emotions), stress_pct, dominant, f"preserved: {reason}"
        # No prior state — use lexicon
        emotions, raw_stress = analyze_text(latest_text)
        stress_pct = int(min(100, raw_stress * 15))
        dominant = max(emotions, key=lambda k: emotions[k])
        print(f"[emotion/groq] fallback ({reason}) → dominant={dominant} stress={stress_pct}%")
        return emotions, stress_pct, dominant, f"lexicon fallback: {reason}"

    if not GROQ_API_KEY:
        return fallback("no API key")

    try:
        context = "\n".join(f"  - {t}" for t in transcript[-10:])
        prev_str = ", ".join(f"{k}: {v:.2f}" for k, v in prev_emotions.items())
        user_msg = (
            f"Lead context: {lead_info or 'Not provided'}\n\n"
            f"Previous emotion state: {prev_str}\n\n"
            f"Conversation so far:\n{context}\n\n"
            f"Latest utterance: \"{latest_text}\"\n\n"
            f"Analyze the customer's current emotional state."
        )
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 150,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": EMOTION_AGENT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            resp.raise_for_status()
            data = json.loads(resp.json()["choices"][0]["message"]["content"])

            # ── Validate and clamp every field ────────────────────────────
            emotions_raw = data.get("emotions", {})
            if not isinstance(emotions_raw, dict):
                return fallback("emotions not a dict")

            emotions = {}
            for e in VALID_EMOTIONS:
                val = emotions_raw.get(e, 0.0)
                if not isinstance(val, (int, float)):
                    val = 0.0
                emotions[e] = float(max(0.0, min(1.0, val)))

            if set(emotions.keys()) != VALID_EMOTIONS:
                return fallback("missing emotion keys")

            stress_raw = data.get("stress", 0)
            if not isinstance(stress_raw, (int, float)):
                return fallback("stress not a number")
            stress_pct = int(max(0, min(100, stress_raw)))

            dominant = data.get("dominant", "")
            if dominant not in VALID_EMOTIONS:
                dominant = max(emotions, key=lambda k: emotions[k])

            reasoning = str(data.get("reasoning", ""))[:200]

            print(f"[emotion/groq] dominant={dominant} stress={stress_pct}% | {reasoning}")
            return emotions, stress_pct, dominant, reasoning

    except asyncio.TimeoutError:
        return fallback("timeout")
    except (KeyError, json.JSONDecodeError) as e:
        return fallback(f"parse error: {e}")
    except Exception as e:
        return fallback(f"{type(e).__name__}: {e}")


async def run_decision_agent(
    latest_text: str,
    transcript: list,
    emotions: dict,
    stress_pct: int,
    dominant: str,
    emotion_reasoning: str,
    lead_info: str,
) -> list[dict]:
    """
    Groq tool-calling agent decides what action to take after each utterance.
    Returns list of {name, args} dicts — one per tool call.
    Falls back to empty list (no action) on error or timeout.
    """
    if not GROQ_API_KEY:
        return []

    try:
        context = "\n".join(f"  - {t}" for t in transcript[-10:])
        emotion_str = " | ".join(f"{k}: {v:.2f}" for k, v in emotions.items())
        user_msg = (
            f"Lead context: {lead_info or 'Not provided'}\n\n"
            f"Emotion scores: {emotion_str}\n"
            f"Stress level: {stress_pct}% | Dominant: {dominant}\n"
            f"Emotion reasoning: {emotion_reasoning}\n\n"
            f"Call transcript (latest at bottom):\n{context}\n\n"
            f"Latest utterance: \"{latest_text}\"\n\n"
            f"What action should be taken right now?"
        )
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "max_tokens": 250,
                    "temperature": 0.2,
                    "tools": DECISION_AGENT_TOOLS,
                    "tool_choice": "required",
                    "messages": [
                        {"role": "system", "content": DECISION_AGENT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            resp.raise_for_status()
            tool_calls = resp.json()["choices"][0]["message"].get("tool_calls", [])

            results = []
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}

                # ── Per-tool argument validation ───────────────────────────
                if name == "fire_coaching_tip":
                    tip = str(args.get("tip", "")).strip()
                    urgency = args.get("urgency", "medium")
                    if not tip:
                        print("[agent] empty tip rejected")
                        continue
                    if len(tip.split()) > 20:
                        tip = " ".join(tip.split()[:15])  # hard cap
                    if urgency not in ("low", "medium", "high"):
                        urgency = "medium"
                    args = {"tip": tip, "urgency": urgency}

                elif name == "escalate_to_management":
                    reason = str(args.get("reason", "")).strip()
                    severity = args.get("severity", "warning")
                    if not reason:
                        print("[agent] empty escalation reason rejected")
                        continue
                    if severity not in ("warning", "critical"):
                        severity = "warning"
                    args = {"reason": reason, "severity": severity}

                elif name == "hold":
                    args = {}

                else:
                    print(f"[agent] unknown tool name '{name}' rejected")
                    continue

                results.append({"name": name, "args": args})
                print(f"[agent] → {name}({args})")

            return results

    except asyncio.TimeoutError:
        print("[agent] decision timeout — no action taken")
        return []
    except Exception as e:
        print(f"[agent] error: {type(e).__name__}: {e} — no action taken")
        return []


async def stream_tts_to_ws(ws: WebSocket, text: str):
    """Call Lightning TTS → get WAV bytes → send to browser."""
    print(f"[tts] requesting: \"{text[:80]}\"")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TTS_URL,
                headers={"Authorization": f"Bearer {SMALLEST_API_KEY}", "Content-Type": "application/json"},
                json={"text": text, "voice_id": "magnus", "sample_rate": 24000, "speed": 1.0, "language": "en", "output_format": "wav"},
            )
            print(f"[tts] response status={resp.status_code} content_type={resp.headers.get('content-type')} size={len(resp.content)} bytes")
            resp.raise_for_status()
            await ws.send_bytes(resp.content)
            print(f"[tts] sent {len(resp.content)} bytes to browser")
        await ws.send_text(json.dumps({"type": "tts_done"}))
    except httpx.HTTPStatusError as e:
        print(f"[tts] error: {e} — body: {e.response.text}")
    except Exception as e:
        print(f"[tts] error: {e}")


# ── Main WebSocket handler ────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    print("[ws] browser connected")
    active_rep["ws"] = ws

    audio_q: asyncio.Queue = asyncio.Queue()
    running = {"v": True}

    # State for emotion + agent tracking
    transcript_buffer = deque(maxlen=10)
    smooth_emotions = {e: 0.0 for e in EMOTIONS}
    last_trigger = 0.0
    last_escalation = 0.0
    last_groq_emotion = 0.0
    COOLDOWN = 10.0           # min seconds between coaching tip actions
    ESCALATION_COOLDOWN = 10.0  # min seconds between escalation broadcasts
    EMOTION_COOLDOWN = 3.0    # min seconds between Groq emotion API calls

    async def receive_from_browser():
        """Receive audio chunks (binary) or control messages (text) from browser."""
        chunk_count = 0
        try:
            while running["v"]:
                msg = await ws.receive()
                if "bytes" in msg and msg["bytes"]:
                    chunk_count += 1
                    if chunk_count % 100 == 0:
                        print(f"[audio] {chunk_count} chunks received, q_size={audio_q.qsize()}")
                    await audio_q.put(msg["bytes"])
                elif "text" in msg:
                    data = json.loads(msg["text"])
                    msg_type = data.get("type")
                    print(f"[ws] text message: type={msg_type}")
                    if msg_type == "stop":
                        print("[ws] stop received — shutting down")
                        running["v"] = False
                    elif msg_type == "lead_info":
                        active_rep["lead_info"] = data.get("text", "")
                        print(f"[lead] saved: {active_rep['lead_info'][:60]}...")
                    elif msg_type == "query":
                        question = data.get("text", "").strip()
                        lead_info = data.get("lead_info", active_rep["lead_info"])
                        print(f"[query] rep asked: '{question}'")
                        print(f"[query] lead_info: '{lead_info[:60] if lead_info else 'none'}'")
                        print(f"[query] transcript context: {len(transcript_buffer)} lines")
                        if question:
                            answer = await get_groq_answer(question, list(transcript_buffer), lead_info)
                            print(f"[query] answer: {answer}")
                            await ws.send_text(json.dumps({"type": "answer", "text": answer, "question": question}))
                        else:
                            print("[query] empty question, skipping")
        except WebSocketDisconnect:
            print(f"[ws] browser disconnected (received {chunk_count} audio chunks total)")
            running["v"] = False
        except Exception as e:
            print(f"[ws] receive error: {type(e).__name__}: {e}")
            running["v"] = False

    async def pulse_stt():
        """Stream audio to Pulse STT WebSocket, emit transcript events back to browser."""
        nonlocal last_trigger, smooth_emotions
        url = f"{STT_WS_URL}?language=en&encoding=linear16&sample_rate=16000"
        headers = {"Authorization": f"Bearer {SMALLEST_API_KEY}"}
        print(f"[stt] connecting to {url}")
        print(f"[stt] API key: {SMALLEST_API_KEY[:8]}...{SMALLEST_API_KEY[-4:] if SMALLEST_API_KEY else 'MISSING'}")

        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=30) as stt_ws:
                print("[stt] connected to Pulse")

                async def send_audio():
                    import struct
                    sent = 0
                    silent_chunks = 0
                    while running["v"]:
                        try:
                            chunk = await asyncio.wait_for(audio_q.get(), timeout=0.5)
                            # Sample up to 32 int16 samples to check amplitude
                            n = min(len(chunk) // 2, 32)
                            if n > 0:
                                samples = struct.unpack(f"<{n}h", chunk[:n * 2])
                                max_amp = max(abs(s) for s in samples)
                            else:
                                max_amp = 0
                            if max_amp < 50:
                                silent_chunks += 1
                            await stt_ws.send(chunk)
                            sent += 1
                            if sent % 100 == 0:
                                print(f"[stt] sent {sent} chunks | size={len(chunk)}B | max_amp={max_amp} | silent={silent_chunks}/100")
                                silent_chunks = 0
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            break
                        except Exception as e:
                            print(f"[stt] send_audio error: {e}")
                            break

                async def recv_transcripts():
                    nonlocal last_trigger, last_escalation, last_groq_emotion, smooth_emotions
                    msg_count = 0
                    async for message in stt_ws:
                        msg_count += 1
                        try:
                            data = json.loads(message)
                            if msg_count <= 3:
                                print(f"[stt] raw message #{msg_count}: {data}")
                            text = data.get("transcript") or data.get("text") or ""
                            is_final = data.get("is_final", True)

                            if not text.strip():
                                print(f"[stt] empty transcript, skipping (msg #{msg_count})")
                                continue

                            print(f"[stt] {'FINAL' if is_final else 'partial'}: \"{text}\"")

                            # Send transcript to browser
                            await ws.send_text(json.dumps({
                                "type": "transcript",
                                "text": text,
                                "is_final": is_final,
                            }))

                            if not is_final:
                                continue

                            # ── Groq emotion agent (rate-limited, lexicon fallback) ───────
                            transcript_buffer.append(text)
                            now = time.time()
                            if (now - last_groq_emotion) >= EMOTION_COOLDOWN:
                                last_groq_emotion = now
                                emotions, stress_pct, dominant, emotion_reasoning = await analyze_emotion_groq(
                                    text,
                                    list(transcript_buffer),
                                    active_rep["lead_info"],
                                    smooth_emotions,
                                )
                            else:
                                # Too soon — use lexicon for quick update, preserve stress trajectory
                                lex_emotions, raw_stress = analyze_text(text)
                                dominant = max(smooth_emotions, key=lambda k: smooth_emotions[k])
                                stress_pct = int(min(100, (smooth_emotions.get("anger", 0) + smooth_emotions.get("disgust", 0) + smooth_emotions.get("fear", 0)) * 100))
                                emotions = lex_emotions
                                emotion_reasoning = "throttled — using lexicon"
                                print(f"[emotion] throttled (cooldown) → lexicon dominant={dominant} stress={stress_pct}%")

                            # Smooth with EMA so UI doesn't jump
                            for e in EMOTIONS:
                                smooth_emotions[e] = 0.6 * smooth_emotions[e] + 0.4 * emotions[e]

                            await ws.send_text(json.dumps({
                                "type": "emotion",
                                "emotions": smooth_emotions,
                                "stress": stress_pct,
                            }))

                            # ── Decision agent (tool-calling) ─────────────────────────
                            if (now - last_trigger) >= COOLDOWN:
                                actions = await run_decision_agent(
                                    text,
                                    list(transcript_buffer),
                                    smooth_emotions,
                                    stress_pct,
                                    dominant,
                                    emotion_reasoning,
                                    active_rep["lead_info"],
                                )
                                for action in actions:
                                    name = action["name"]
                                    args = action["args"]

                                    if name == "fire_coaching_tip":
                                        last_trigger = now
                                        tip = args["tip"]
                                        urgency = args["urgency"]
                                        print(f"[coach] tip (urgency={urgency}): {tip}")
                                        await ws.send_text(json.dumps({"type": "tip", "text": tip}))
                                        # await stream_tts_to_ws(ws, tip)  # TTS disabled for demo

                                    elif name == "escalate_to_management":
                                        if management_connections and (now - last_escalation) >= ESCALATION_COOLDOWN:
                                            last_escalation = now
                                            severity = args["severity"]
                                            reason = args["reason"]
                                            print(f"[escalate] → management (severity={severity}): {reason}")
                                            await broadcast_to_management({
                                                "type": "escalation",
                                                "reason": reason,
                                                "severity": severity,
                                                "trigger_text": text,
                                                "emotion": dominant,
                                                "stress_pct": stress_pct,
                                                "transcript": list(transcript_buffer)[-5:],
                                                "lead_info": active_rep["lead_info"],
                                                "ts": time.strftime("%H:%M:%S"),
                                            })

                                    elif name == "hold":
                                        print("[agent] hold — no action needed")

                        except Exception as e:
                            print(f"[stt] parse error: {e}")

                await asyncio.gather(send_audio(), recv_transcripts())

        except Exception as e:
            import traceback
            print(f"[stt] connection error: {type(e).__name__}: {e}")
            traceback.print_exc()

    await asyncio.gather(receive_from_browser(), pulse_stt())
    print("[ws] browser disconnected")
