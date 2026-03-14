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

    # State for emotion tracking
    transcript_buffer = deque(maxlen=10)
    stress_window = deque(maxlen=3)
    smooth_emotions = {e: 0.0 for e in EMOTIONS}
    last_trigger = 0.0
    last_escalation = 0.0
    COOLDOWN = 10.0
    THRESHOLD = 1.5
    ESCALATION_THRESHOLD = 1.5
    ESCALATION_COOLDOWN = 10.0

    OBJECTION_PATTERNS = [
        r"\b(not interested|no longer interested)\b",
        r"\b(too expensive|can.t afford|no budget|out of budget)\b",
        r"\b(going with|switching to|already using|chose another)\b",
        r"\b(cancel|cancelling|canceling)\b",
        r"\b(not the right fit|doesn.t work for us|not a good fit)\b",
        r"\b(competitor|alternative|other (vendor|option|solution))\b",
        r"\b(lawsuit|lawyer|sue|legal action)\b",
    ]

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
                    nonlocal last_trigger, last_escalation, smooth_emotions
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

                            # Emotion analysis
                            transcript_buffer.append(text)
                            emotions, stress = analyze_text(text)
                            stress_window.append(stress)
                            cumulative = sum(stress_window)

                            for e in EMOTIONS:
                                smooth_emotions[e] = 0.6 * smooth_emotions[e] + 0.4 * emotions[e]

                            stress_pct = int(min(100, cumulative * 15))
                            print(f"[emotion] stress={stress:.2f} cumulative={cumulative:.2f} pct={stress_pct}% dominant={max(smooth_emotions, key=lambda k: smooth_emotions[k])}")
                            await ws.send_text(json.dumps({
                                "type": "emotion",
                                "emotions": smooth_emotions,
                                "stress": stress_pct,
                            }))

                            # Coaching trigger
                            now = time.time()
                            dominant = max(smooth_emotions, key=lambda k: smooth_emotions[k])
                            since_trigger = now - last_trigger
                            trigger_ok = (stress >= THRESHOLD or cumulative >= THRESHOLD * 1.5)
                            cooldown_ok = since_trigger >= COOLDOWN
                            print(f"[coach] check: trigger={trigger_ok} (stress={stress:.2f}>={THRESHOLD} or cum={cumulative:.2f}>={THRESHOLD*1.5:.2f}) cooldown={cooldown_ok} ({since_trigger:.1f}s ago)")
                            if trigger_ok and cooldown_ok:
                                last_trigger = now
                                context = list(transcript_buffer)[-5:]
                                print(f"[coach] triggered — stress={stress:.2f}, emotion={dominant}")

                                # Get tip (Groq or template)
                                tip = await get_groq_tip(context, dominant)
                                print(f"[coach] tip: {tip}")

                                # Send tip text to browser
                                await ws.send_text(json.dumps({"type": "tip", "text": tip}))

                                # Stream TTS chunks to browser as they arrive
                                await stream_tts_to_ws(ws, tip)

                            # Escalation to management
                            objection_hit = next(
                                (p for p in OBJECTION_PATTERNS if re.search(p, text, re.IGNORECASE)), None
                            )
                            if objection_hit:
                                print(f"[escalate] objection pattern matched: {objection_hit}")
                            print(f"[escalate] check: mgmt_connections={len(management_connections)} cumulative={cumulative:.2f}>={ESCALATION_THRESHOLD}? objection={bool(objection_hit)} cooldown_ok={(now - last_escalation)>=ESCALATION_COOLDOWN}")
                            if management_connections and (now - last_escalation) >= ESCALATION_COOLDOWN:
                                if cumulative >= ESCALATION_THRESHOLD or objection_hit:
                                    last_escalation = now
                                    reason = "high stress" if not objection_hit else f"objection detected"
                                    snippet = list(transcript_buffer)[-5:]
                                    print(f"[escalate] → management: {reason}")
                                    await broadcast_to_management({
                                        "type": "escalation",
                                        "reason": reason,
                                        "trigger_text": text,
                                        "emotion": dominant,
                                        "stress_pct": int(min(100, cumulative * 15)),
                                        "transcript": snippet,
                                        "lead_info": active_rep["lead_info"],
                                        "ts": time.strftime("%H:%M:%S"),
                                    })

                        except Exception as e:
                            print(f"[stt] parse error: {e}")

                await asyncio.gather(send_audio(), recv_transcripts())

        except Exception as e:
            import traceback
            print(f"[stt] connection error: {type(e).__name__}: {e}")
            traceback.print_exc()

    await asyncio.gather(receive_from_browser(), pulse_stt())
    print("[ws] browser disconnected")
