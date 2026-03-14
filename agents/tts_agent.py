"""
TTS Agent — streams coaching tips via Smallest AI Lightning TTS (SSE).

The API returns Server-Sent Events with JSON: {"audio": "<base64 PCM>", "done": bool}
We decode each chunk and play it immediately for low-latency streaming audio.
"""

import asyncio
import base64
import os
import threading

import pyaudio
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

TTS_URL = "https://api.smallest.ai/waves/v1/lightning/stream"
API_KEY = os.getenv("SMALLEST_API_KEY", "")

SAMPLE_RATE = 24000
CHANNELS = 1
FORMAT = pyaudio.paInt16


class TTSAgent:
    def __init__(
        self,
        tts_queue: asyncio.Queue,
        output_device_index: int | None = None,
    ):
        self.tts_queue = tts_queue
        self.output_device_index = output_device_index
        self._pa = pyaudio.PyAudio()
        self._running = False

    async def run(self):
        self._running = True
        print("[tts] ready, waiting for coaching tips...")
        while self._running:
            try:
                tip = await asyncio.wait_for(self.tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            print(f"[tts] speaking: {tip}")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._speak, tip)

    def _speak(self, text: str):
        """Synthesize via Lightning SSE stream and play audio chunks as they arrive."""
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "voice_id": "emily",
            "sample_rate": SAMPLE_RATE,
            "speed": 1.15,
        }

        try:
            resp = requests.post(
                TTS_URL,
                json=payload,
                headers=headers,
                stream=True,
                timeout=15,
            )
            resp.raise_for_status()

            stream = self._pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                output_device_index=self.output_device_index,
                frames_per_buffer=1024,
            )

            # Parse SSE: each line is "data: <json>" or empty
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if line.startswith("data:"):
                    payload_str = line[5:].strip()
                    if not payload_str or payload_str == "[DONE]":
                        break
                    try:
                        import json
                        event = json.loads(payload_str)
                        if event.get("done"):
                            break
                        audio_b64 = event.get("audio", "")
                        if audio_b64:
                            pcm = base64.b64decode(audio_b64)
                            stream.write(pcm)
                    except Exception:
                        pass

            stream.stop_stream()
            stream.close()
            print("[tts] done speaking")

        except Exception as e:
            print(f"[tts] error: {e}")

    def stop(self):
        self._running = False
        self._pa.terminate()


if __name__ == "__main__":
    import asyncio

    async def _test():
        q: asyncio.Queue = asyncio.Queue()
        agent = TTSAgent(q)
        print("[tts] testing voice...")
        agent._speak("Stay calm, acknowledge their frustration, and offer a clear solution.")
        print("[tts] test complete")

    asyncio.run(_test())
