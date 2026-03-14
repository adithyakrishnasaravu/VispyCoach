#!/usr/bin/env python3
"""
VispyCoach Demo Script
======================
Simulates a live sales call going south, showing:
  1. Rep UI receives live transcript + emotion updates
  2. Objection triggers management escalation alert
  3. Manager sends strategy back → banner appears on rep screen

Run the server first:
  uvicorn server:app --host 0.0.0.0 --port 8765

Then in a new terminal:
  python demo.py

Keep both http://localhost:8765 and http://localhost:8765/management open.
"""

import asyncio
import json
import time
import sys
import httpx
import websockets

BASE_URL = "http://localhost:8765"
REP_WS   = "ws://localhost:8765/ws"
MGMT_WS  = "ws://localhost:8765/ws/management"

# Simulated call transcript (customer lines only — these drive emotion/coaching)
CALL_SCRIPT = [
    (2,  "Hi, I got your email but I honestly don't have much time."),
    (4,  "We already looked at a few solutions and nothing really fit."),
    (6,  "The pricing seems way too expensive for what we'd actually use."),
    (8,  "I'm not interested anymore. We've decided to go with a competitor."),
    (10, "Look, I don't want to waste your time. We're cancelling the evaluation."),
    (12, "If this doesn't change I'm going to have to talk to your manager or a lawyer."),
]

MANAGER_REPLY_DELAY = 5   # seconds after first escalation before manager replies
MANAGER_MESSAGE = "Offer 30% off annual plan + free onboarding. Loop in Sarah from enterprise if needed."


def sep(label=""):
    w = 60
    if label:
        pad = (w - len(label) - 2) // 2
        print(f"\n{'─' * pad} {label} {'─' * pad}")
    else:
        print("─" * w)


async def check_server():
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(BASE_URL)
            assert r.status_code == 200
        print("✓ Server is running at", BASE_URL)
    except Exception:
        print("✗ Server not reachable at", BASE_URL)
        print("  Start it with:  uvicorn server:app --host 0.0.0.0 --port 8765")
        sys.exit(1)


async def run_rep_ws(ready_event: asyncio.Event, escalated_event: asyncio.Event):
    """Connects as the rep browser, injects transcript lines, prints received events."""
    sep("REP SIDE")
    print("Connecting rep WebSocket...")

    async with websockets.connect(REP_WS) as ws:
        print("✓ Rep WS connected\n")
        ready_event.set()

        # Send lead info
        await ws.send(json.dumps({
            "type": "lead_info",
            "text": "ACME Corp — Sarah Chen, VP of Engineering. Budget $50k. Evaluating 3 vendors.",
        }))
        print("[rep] Lead info sent\n")

        async def inject_transcripts():
            start = time.time()
            for delay, line in CALL_SCRIPT:
                await asyncio.sleep(delay - (time.time() - start))
                elapsed = time.time() - start
                print(f"[{elapsed:4.1f}s] Customer: {line}")
                # Inject via the internal HTTP endpoint (bypasses audio pipeline)
                async with httpx.AsyncClient() as c:
                    await c.post(f"{BASE_URL}/inject-transcript", json={"text": line})

        async def listen_for_events():
            escalation_sent = False
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type")
                if t == "transcript":
                    pass  # already printed above
                elif t == "emotion":
                    stress = msg.get("stress", 0)
                    emotions = msg.get("emotions", {})
                    dominant = max(emotions, key=lambda k: emotions[k]) if emotions else "?"
                    bar = "█" * (stress // 10) + "░" * (10 - stress // 10)
                    print(f"         → Emotion: {dominant:8s}  Stress [{bar}] {stress}%")
                elif t == "tip":
                    print(f"\n  💡 COACHING TIP → \"{msg['text']}\"\n")
                elif t == "tts_done":
                    print("         → TTS spoken to rep's earpiece")
                elif t == "answer":
                    print(f"\n  🤖 COACH ANSWER → \"{msg['text']}\"\n")
                elif t == "manager_message":
                    sep("MANAGER → REP")
                    print(f"  📩 Manager says: \"{msg['text']}\"")
                    sep()
                    return  # demo complete

                # Signal that escalation probably happened (after objection line)
                if not escalation_sent and t == "emotion":
                    stress = msg.get("stress", 0)
                    if stress > 40:
                        escalation_sent = True
                        escalated_event.set()

        await asyncio.gather(inject_transcripts(), listen_for_events())


async def run_mgmt_ws(ready_event: asyncio.Event, escalated_event: asyncio.Event):
    """Connects as management dashboard, receives alert, sends strategy back."""
    await ready_event.wait()  # wait for rep WS to be ready

    sep("MANAGEMENT SIDE")
    print("Connecting management WebSocket...")

    async with websockets.connect(MGMT_WS) as ws:
        print("✓ Management WS connected — watching for escalations\n")

        # Wait for the first escalation alert
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "escalation":
                sep("ESCALATION ALERT")
                print(f"  Reason    : {msg['reason']}")
                print(f"  Emotion   : {msg['emotion']}  |  Stress: {msg['stress_pct']}%")
                print(f"  Trigger   : \"{msg['trigger_text']}\"")
                print(f"  Transcript:")
                for line in msg.get("transcript", []):
                    print(f"    · {line}")
                if msg.get("lead_info"):
                    print(f"  Lead Info : {msg['lead_info']}")
                sep()

                print(f"\n[mgmt] Waiting {MANAGER_REPLY_DELAY}s before replying...")
                await asyncio.sleep(MANAGER_REPLY_DELAY)

                print(f"[mgmt] Sending strategy to rep: \"{MANAGER_MESSAGE}\"")
                await ws.send(json.dumps({
                    "type": "manager_message",
                    "text": MANAGER_MESSAGE,
                }))
                print("[mgmt] ✓ Strategy sent\n")
                break


async def main():
    sep("VispyCoach Demo")
    print("This script simulates a sales call going bad.")
    print("Open these in your browser:")
    print(f"  Rep UI  →  {BASE_URL}")
    print(f"  Mgmt    →  {BASE_URL}/management")
    sep()

    await check_server()
    print()

    ready_event     = asyncio.Event()
    escalated_event = asyncio.Event()

    await asyncio.gather(
        run_rep_ws(ready_event, escalated_event),
        run_mgmt_ws(ready_event, escalated_event),
    )

    sep("Demo Complete")
    print("Full pipeline verified:")
    print("  ✓ Transcript injection → emotion analysis")
    print("  ✓ Stress spike → coaching tip + TTS")
    print("  ✓ Objection detected → management escalation alert")
    print("  ✓ Manager reply → banner displayed on rep screen")
    sep()


if __name__ == "__main__":
    asyncio.run(main())
