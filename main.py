"""
WhisperCoach — Real-Time AI Call Coach
=======================================
Entry point. Launches 4 background agents and the Gradio dashboard.

Agents (all running as asyncio tasks):
  1. STTAgent      — streams mic audio to Pulse WebSocket → emits transcript+emotions
  2. EmotionAgent  — monitors emotions, triggers on stress spikes
  3. CoachAgent    — generates 1-sentence tip via Claude Haiku
  4. TTSAgent      — speaks tip via Lightning TTS into earpiece

Usage:
  python main.py
  python main.py --list-devices       # show audio device indices
  python main.py --output-device 2    # use device 2 for TTS output (earpiece)
"""

import argparse
import asyncio
import threading

from dotenv import load_dotenv

load_dotenv(override=True)

from agents.stt_agent import STTAgent
from agents.emotion_agent import EmotionAgent
from agents.coach_agent import CoachAgent
from agents.tts_agent import TTSAgent
from audio.capture import MicCapture
from ui.dashboard import build_ui

# ── Shared state (mutated by agents, read by UI poll) ─────────────────────────
ui_state: dict = {
    "transcript": [],
    "emotions": {"happiness": 0, "sadness": 0, "anger": 0, "fear": 0, "disgust": 0},
    "stress": 0,
    "tips": [],
}

# ── Queues connecting agents ──────────────────────────────────────────────────
audio_queue: asyncio.Queue = None
stt_queue: asyncio.Queue = None
coach_queue: asyncio.Queue = None
tts_queue: asyncio.Queue = None

# ── Agent instances ───────────────────────────────────────────────────────────
mic: MicCapture = None
stt_agent: STTAgent = None
emotion_agent: EmotionAgent = None
coach_agent: CoachAgent = None
tts_agent: TTSAgent = None

# ── Asyncio event loop running in background thread ───────────────────────────
_loop: asyncio.AbstractEventLoop = None
_bg_thread: threading.Thread = None
_tasks: list = []
_running = False


def _init_queues(loop):
    global audio_queue, stt_queue, coach_queue, tts_queue
    audio_queue = asyncio.Queue()
    stt_queue = asyncio.Queue()
    coach_queue = asyncio.Queue()
    tts_queue = asyncio.Queue()


def _build_agents(output_device: int | None, input_device: int | None = None):
    global mic, stt_agent, emotion_agent, coach_agent, tts_agent
    mic = MicCapture(audio_queue, device_index=input_device)
    stt_agent = STTAgent(audio_queue, stt_queue)
    emotion_agent = EmotionAgent(stt_queue, coach_queue, ui_state)
    coach_agent = CoachAgent(coach_queue, tts_queue, ui_state)
    tts_agent = TTSAgent(tts_queue, output_device_index=output_device)


async def _run_all():
    global _tasks
    _tasks = [
        asyncio.create_task(stt_agent.run(), name="stt"),
        asyncio.create_task(emotion_agent.run(), name="emotion"),
        asyncio.create_task(coach_agent.run(), name="coach"),
        asyncio.create_task(tts_agent.run(), name="tts"),
    ]
    await asyncio.gather(*_tasks, return_exceptions=True)


def _bg_loop_runner():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def on_start():
    global _running
    if _running:
        return
    _running = True
    print("[main] starting WhisperCoach...")
    mic.start(_loop)
    asyncio.run_coroutine_threadsafe(_run_all(), _loop)
    print("[main] all agents started")


def on_stop():
    global _running
    if not _running:
        return
    _running = False
    print("[main] stopping...")
    mic.stop()
    for agent in [stt_agent, emotion_agent, coach_agent, tts_agent]:
        if agent:
            agent.stop()
    for task in _tasks:
        task.cancel()
    print("[main] stopped")


def main():
    global _loop, _bg_thread

    parser = argparse.ArgumentParser(description="WhisperCoach — Real-Time AI Call Coach")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--input-device", type=int, default=None, help="Audio input device index (e.g. BlackHole for call audio)")
    parser.add_argument("--output-device", type=int, default=None, help="Audio output device index for TTS (earpiece)")
    args = parser.parse_args()

    if args.list_devices:
        MicCapture.list_devices()
        return

    # Start background asyncio event loop
    _loop = asyncio.new_event_loop()
    _bg_thread = threading.Thread(target=_bg_loop_runner, daemon=True)
    _bg_thread.start()

    # Init queues and agents in the bg loop context
    _loop.call_soon_threadsafe(_init_queues, _loop)
    import time; time.sleep(0.1)  # let queues init

    _init_queues(_loop)
    _build_agents(args.output_device, args.input_device)

    # Build and launch Gradio
    demo = build_ui(ui_state, on_start=on_start, on_stop=on_stop)
    print("\n🎧 WhisperCoach is starting...")
    print("   Open http://localhost:7860 in your browser")
    print("   Press the ▶ Start button to begin listening\n")
    demo.launch(server_port=7860, share=False, show_error=True)


if __name__ == "__main__":
    main()
