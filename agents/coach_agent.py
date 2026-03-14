"""
Coach Agent — generates contextual coaching tips based on detected emotion and transcript keywords.

Uses a smart template system (no LLM API call needed) for instant, zero-latency tips.
Tips are selected based on dominant emotion + detected trigger keywords in the context.
"""

import asyncio
import os
import random
import re
import time
from dotenv import load_dotenv

load_dotenv(override=True)

# Optional: Groq for contextual LLM tips (free tier — sign up at console.groq.com)
_groq_client = None
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if GROQ_API_KEY:
    try:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
        print("[coach] Groq LLM enabled (llama-3.1-8b-instant)")
    except Exception as e:
        print(f"[coach] Groq init failed: {e}, falling back to templates")

GROQ_SYSTEM = """You are WhisperCoach, a real-time AI coach for sales and support reps on live calls.
Your tip will be SPOKEN ALOUD into the rep's earpiece. They cannot read it.

Rules (STRICT):
- ONE sentence. Maximum 12 words.
- Reference something the customer actually said.
- Be specific, calm, and actionable.
- No filler words. No "Great!" or "Remember to...".

Bad: "Stay calm and offer a solution."
Good: "Acknowledge the two-week wait and give a specific date for resolution."
"""

# ── Tip library — keyed by (keyword_pattern, emotion) priority order ──────────
KEYWORD_TIPS = [
    # Refund / money demands
    (r"\brefund\b", "anger",
     "Offer a full refund immediately and apologize for the inconvenience."),
    (r"\brefund\b", None,
     "Process the refund now and confirm the timeline clearly."),

    # Cancel / leaving
    (r"\bcancel\b", "anger",
     "Acknowledge their desire to leave, then offer one concrete reason to stay."),

    # Legal threats
    (r"\b(lawyer|lawsuit|sue|legal)\b", "anger",
     "Escalate to your supervisor right now and stay calm and professional."),

    # Manager / escalation requests
    (r"\b(manager|supervisor|escalate)\b", None,
     "Offer to escalate immediately and set a clear callback time."),

    # Waiting / time frustration
    (r"\b(wait|waiting|weeks|days|months)\b", "anger",
     "Apologize for the delay and give them a specific resolution date."),

    # Repeat caller
    (r"\b(again|third|fourth|multiple|times)\b", "anger",
     "Acknowledge they've called before and take personal ownership this time."),

    # Product broken / not working
    (r"\b(broken|doesn.t work|not working|useless|defective)\b", None,
     "Empathize, then walk them through one concrete fix or replacement option."),

    # Nobody helping
    (r"\b(nobody|no one|ignored|helpless)\b", None,
     "Say: I hear you, and I'm personally going to fix this for you right now."),
]

# ── Fallback tips by dominant emotion ─────────────────────────────────────────
EMOTION_TIPS = {
    "anger": [
        "Lower your tone, say their name, and acknowledge their frustration directly.",
        "Say: I completely understand, let me make this right for you right now.",
        "Slow down, take a breath, and offer one concrete next step.",
        "Validate their feelings first before jumping to solutions.",
        "Apologize sincerely, then ask: what would make this right for you?",
    ],
    "disgust": [
        "Acknowledge the issue seriously and avoid minimizing their experience.",
        "Show genuine empathy and outline a clear path to resolution.",
        "Say: That should never have happened, and I'm going to fix it now.",
    ],
    "fear": [
        "Reassure them clearly: your account is safe and I'll resolve this today.",
        "Be specific about next steps so they feel in control of the situation.",
        "Say: I understand your concern, here's exactly what will happen next.",
    ],
    "sadness": [
        "Show genuine empathy before moving to any solution.",
        "Say: I'm really sorry this happened, that's completely understandable.",
        "Take a moment to listen fully before offering any resolution.",
    ],
    "happiness": [
        "Great moment to confirm satisfaction and ask if there's anything else.",
        "Reinforce the positive and summarize what was resolved.",
    ],
}

DEFAULT_TIPS = [
    "Stay calm, acknowledge their concern, and offer a clear solution.",
    "Listen actively, then repeat back what you heard before responding.",
    "Take ownership of the issue and give a specific resolution timeline.",
]


class CoachAgent:
    def __init__(
        self,
        coach_queue: asyncio.Queue,
        tts_queue: asyncio.Queue,
        ui_state: dict,
    ):
        self.coach_queue = coach_queue
        self.tts_queue = tts_queue
        self.ui_state = ui_state
        self._running = False

    async def run(self):
        self._running = True
        print("[coach] ready, waiting for emotion triggers...")
        while self._running:
            try:
                trigger = await asyncio.wait_for(self.coach_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            tip = await self._get_tip(trigger)
            print(f"[coach] 💡 tip: {tip}")
            await self.tts_queue.put(tip)
            self._update_ui(tip)

    async def _get_tip(self, trigger: dict) -> str:
        # If Groq is available, use LLM for contextual, personalized tips
        if _groq_client:
            try:
                context = "\n".join(f"- {t}" for t in trigger.get("context", []))
                emotion = trigger.get("dominant_emotion", "anger")
                loop = asyncio.get_event_loop()
                tip = await loop.run_in_executor(None, self._groq_tip, context, emotion)
                if tip:
                    return tip
            except Exception as e:
                print(f"[coach] Groq error: {e}")
        return self._select_tip(trigger)

    def _groq_tip(self, context: str, emotion: str) -> str:
        resp = _groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            max_tokens=40,
            temperature=0.4,
            messages=[
                {"role": "system", "content": GROQ_SYSTEM},
                {"role": "user", "content": f"Emotion: {emotion}\n\nCustomer said:\n{context}\n\nCoaching tip:"},
            ],
        )
        return resp.choices[0].message.content.strip()

    def _select_tip(self, trigger: dict) -> str:
        context = " ".join(trigger.get("context", []))
        emotion = trigger.get("dominant_emotion", "anger")

        # 1. Try keyword-specific tips first (highest priority)
        for pattern, tip_emotion, tip in KEYWORD_TIPS:
            if re.search(pattern, context, re.IGNORECASE):
                if tip_emotion is None or tip_emotion == emotion:
                    return tip

        # 2. Fall back to emotion-based tips
        tips = EMOTION_TIPS.get(emotion, DEFAULT_TIPS)
        return random.choice(tips)

    def _update_ui(self, tip: str):
        timestamp = time.strftime("%H:%M:%S")
        self.ui_state.setdefault("tips", []).append(f"[{timestamp}] {tip}")
        self.ui_state["tips"] = self.ui_state["tips"][-5:]

    def stop(self):
        self._running = False
