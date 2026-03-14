"""
STT Agent — streams PCM audio to Smallest AI Pulse via WebSocket.
Emits transcript + emotion events into stt_queue.

Event format pushed to stt_queue:
{
    "text": str,
    "speaker": str,
    "emotions": {
        "happiness": float,
        "sadness": float,
        "anger": float,
        "fear": float,
        "disgust": float,
    },
    "is_final": bool,
    "timestamp": float,
}
"""

import asyncio
import json
import os
import time

import websockets
from dotenv import load_dotenv

load_dotenv(override=True)

STT_WS_URL = "wss://waves-api.smallest.ai/api/v1/pulse/get_text"
API_KEY = os.getenv("SMALLEST_API_KEY", "")


class STTAgent:
    def __init__(self, audio_queue: asyncio.Queue, stt_queue: asyncio.Queue):
        self.audio_queue = audio_queue  # raw PCM bytes in
        self.stt_queue = stt_queue      # parsed events out
        self._running = False

    async def run(self):
        self._running = True
        url = f"{STT_WS_URL}?language=en&encoding=linear16&sample_rate=16000&diarize=true&detect_emotions=true"
        headers = {"Authorization": f"Bearer {API_KEY}"}

        print("[stt] connecting to Pulse WebSocket...")
        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=30) as ws:
                print("[stt] connected")
                # Run sender and receiver concurrently
                await asyncio.gather(
                    self._send_audio(ws),
                    self._receive_results(ws),
                )
        except Exception as e:
            print(f"[stt] connection error: {e}")
            self._running = False

    async def _send_audio(self, ws):
        while self._running:
            try:
                chunk = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                await ws.send(chunk)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[stt] send error: {e}")
                break

    async def _receive_results(self, ws):
        async for message in ws:
            try:
                data = json.loads(message)
                # Log raw response once so we can see the full schema
                if not hasattr(self, "_logged_schema"):
                    self._logged_schema = True
                    print(f"[stt] RAW RESPONSE SCHEMA: {json.dumps(data, indent=2)}")
                event = self._parse_response(data)
                if event and event["text"].strip():
                    await self.stt_queue.put(event)
                    print(f"[stt] [{event['speaker']}]: {event['text']}")
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"[stt] parse error: {e}")

    def _parse_response(self, data: dict) -> dict | None:
        # Real-time WebSocket uses "transcript" key
        text = data.get("transcript") or data.get("text") or ""
        if not text:
            return None

        speaker = str(data.get("speaker_id", data.get("speaker", "Customer")))
        is_final = bool(data.get("is_final", data.get("isFinal", True)))

        return {
            "text": text,
            "speaker": speaker,
            "is_final": is_final,
            "timestamp": time.time(),
        }

    def stop(self):
        self._running = False


# ── Standalone test ──────────────────────────────────────────────────────────
async def _test():
    """Test STT with microphone input."""
    from audio.capture import MicCapture

    audio_q: asyncio.Queue = asyncio.Queue()
    stt_q: asyncio.Queue = asyncio.Queue()

    loop = asyncio.get_event_loop()
    mic = MicCapture(audio_q)
    mic.start(loop)

    agent = STTAgent(audio_q, stt_q)
    print("Speak into your mic. Press Ctrl+C to stop.")
    try:
        await agent.run()
    except KeyboardInterrupt:
        mic.stop()


if __name__ == "__main__":
    asyncio.run(_test())
