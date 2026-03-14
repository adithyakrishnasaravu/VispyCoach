"""
Emotion Agent — monitors STT transcript text, detects stress via keyword heuristics.

Since the real-time Pulse WebSocket doesn't return emotion scores, we use a
keyword-weighted stress scorer on the transcript text. This gives instant,
zero-latency emotion signals without extra API calls.

Trigger condition: text_stress_score >= STRESS_THRESHOLD on any final utterance.
Cooldown: COOLDOWN_SECS between triggers so tips don't spam.

Pushes to coach_queue:
{
    "context": [last N utterances as strings],
    "dominant_emotion": str,
    "stress_score": float,
    "timestamp": float,
}
"""

import asyncio
import re
import time
from collections import deque

STRESS_THRESHOLD = 1.5    # keyword score to trigger (lower = more sensitive)
COOLDOWN_SECS = 10.0      # min seconds between coaching tips
BUFFER_SIZE = 10           # utterances to keep for context
CONTEXT_WINDOW = 5         # how many utterances to send to coach
CUMULATIVE_WINDOW = 3      # sum stress across last N utterances for trigger

# Weighted keyword lexicon — higher weight = stronger emotional signal
EMOTION_LEXICON = {
    # High-intensity anger/frustration (weight 3)
    "furious": ("anger", 3), "outraged": ("anger", 3), "livid": ("anger", 3),
    "infuriated": ("anger", 3), "disgusting": ("disgust", 3), "unacceptable": ("anger", 3),
    "appalling": ("anger", 3), "ridiculous": ("anger", 2.5), "absurd": ("anger", 2.5),

    # Medium anger/frustration (weight 2)
    "angry": ("anger", 2), "frustrated": ("anger", 2), "annoyed": ("anger", 2),
    "terrible": ("anger", 2), "awful": ("anger", 2), "horrible": ("anger", 2),
    "hate": ("anger", 2), "useless": ("anger", 2), "incompetent": ("anger", 2),
    "pathetic": ("anger", 2), "waste": ("anger", 1.5), "garbage": ("anger", 2),

    # Demand/threat words (weight 2)
    "refund": ("anger", 2), "cancel": ("anger", 1.5), "lawsuit": ("anger", 3),
    "lawyer": ("anger", 3), "sue": ("anger", 3), "report": ("anger", 1.5),
    "complaint": ("anger", 1.5), "escalate": ("anger", 1.5), "supervisor": ("anger", 1.5),
    "manager": ("anger", 1.5),

    # Repetition/time frustration (weight 1.5)
    "again": ("anger", 1), "third": ("anger", 1.5), "fourth": ("anger", 1.5),
    "times": ("anger", 0.5), "weeks": ("anger", 0.5), "months": ("anger", 0.5),
    "never": ("anger", 1), "always": ("anger", 0.5), "still": ("anger", 1),
    "waiting": ("anger", 1), "nobody": ("anger", 1.5), "ignored": ("anger", 1.5),

    # Fear/concern (weight 1.5)
    "worried": ("fear", 1.5), "scared": ("fear", 2), "terrified": ("fear", 2.5),
    "concerned": ("fear", 1), "anxious": ("fear", 1.5),

    # Sadness/disappointment (weight 1)
    "disappointed": ("sadness", 1.5), "heartbroken": ("sadness", 2),
    "devastated": ("sadness", 2), "upset": ("sadness", 1),
}

EMOTIONS = ["happiness", "sadness", "anger", "fear", "disgust"]


class EmotionAgent:
    def __init__(
        self,
        stt_queue: asyncio.Queue,
        coach_queue: asyncio.Queue,
        ui_state: dict,
    ):
        self.stt_queue = stt_queue
        self.coach_queue = coach_queue
        self.ui_state = ui_state

        self._buffer: deque = deque(maxlen=BUFFER_SIZE)
        self._stress_window: deque = deque(maxlen=CUMULATIVE_WINDOW)  # recent stress scores
        self._last_trigger = 0.0
        self._running = False

        # Smoothed emotion scores for UI display
        self._smooth_emotions = {e: 0.0 for e in EMOTIONS}

    async def run(self):
        self._running = True
        print("[emotion] monitoring customer emotions via text analysis...")
        while self._running:
            try:
                event = await asyncio.wait_for(self.stt_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Only process final utterances (not partials)
            if not event.get("is_final", True):
                continue

            text = event.get("text", "").strip()
            if not text:
                continue

            self._buffer.append(event)

            emotions, stress = self._analyze_text(text)
            self._stress_window.append(stress)
            dominant = max(emotions, key=lambda k: emotions[k])

            # Update smoothed emotions for UI
            for e in EMOTIONS:
                self._smooth_emotions[e] = 0.6 * self._smooth_emotions[e] + 0.4 * emotions[e]

            cumulative = sum(self._stress_window)
            stress_pct = int(min(100, cumulative * 15))
            self._update_ui(event, self._smooth_emotions, stress_pct)
            print(f"[emotion] stress={stress:.2f} cumul={cumulative:.2f} dominant={dominant} | {text[:60]}")

            now = time.time()
            if (stress >= STRESS_THRESHOLD or cumulative >= STRESS_THRESHOLD * 1.5) and (now - self._last_trigger) >= COOLDOWN_SECS:
                self._last_trigger = now
                context = [e["text"] for e in list(self._buffer)[-CONTEXT_WINDOW:]]
                await self.coach_queue.put({
                    "context": context,
                    "dominant_emotion": dominant,
                    "stress_score": stress,
                    "timestamp": now,
                })
                print(f"[emotion] 🚨 TRIGGER — stress={stress:.2f}, emotion={dominant}")

    def _analyze_text(self, text: str) -> tuple[dict, float]:
        """Score emotion from text using keyword lexicon. Returns (emotions_dict, total_stress)."""
        words = re.findall(r"\b\w+\b", text.lower())
        scores = {e: 0.0 for e in EMOTIONS}

        for word in words:
            if word in EMOTION_LEXICON:
                emotion, weight = EMOTION_LEXICON[word]
                scores[emotion] += weight

        # Normalize to 0-1 range for display (cap at 1.0)
        total_stress = scores["anger"] + scores["disgust"] + scores["fear"]
        display = {e: min(1.0, scores[e] / 5.0) for e in EMOTIONS}
        # Happiness is inverse of stress (so chart isn't always empty)
        display["happiness"] = max(0.0, 1.0 - min(1.0, total_stress / 5.0))

        return display, total_stress

    def _update_ui(self, event: dict, emotions: dict, stress: int):
        speaker = event.get("speaker", "Customer")
        text = event.get("text", "")
        self.ui_state.setdefault("transcript", []).append(f"**{speaker}**: {text}")
        self.ui_state["emotions"] = emotions
        self.ui_state["stress"] = stress

    def stop(self):
        self._running = False
