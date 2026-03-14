"""
WhisperCoach Dashboard — sleek black UI with push-to-talk (hold V key).
"""

import gradio as gr
import pandas as pd

EMOTIONS = ["anger", "fear", "disgust", "sadness", "happiness"]
EMOTION_COLORS = {
    "anger": "#ef4444",
    "fear": "#f97316",
    "disgust": "#a855f7",
    "sadness": "#3b82f6",
    "happiness": "#22c55e",
}

CUSTOM_CSS = """
/* ── Global ── */
body, .gradio-container {
    background: #000 !important;
    color: #e5e7eb !important;
    font-family: 'Inter', system-ui, sans-serif !important;
}
.gradio-container { max-width: 1200px !important; margin: 0 auto !important; }

/* ── Header ── */
#wc-header { text-align: center; padding: 24px 0 8px; }
#wc-header h1 { font-size: 2rem; font-weight: 700; color: #fff; letter-spacing: -0.5px; margin: 0; }
#wc-header p  { color: #6b7280; font-size: 0.9rem; margin: 4px 0 0; }

/* ── Status bar ── */
#status-bar {
    display: flex; align-items: center; gap: 12px;
    background: #111; border: 1px solid #222; border-radius: 10px;
    padding: 10px 16px; margin-bottom: 16px;
}
#listen-dot { width: 10px; height: 10px; border-radius: 50%; background: #374151; flex-shrink: 0; }
#listen-dot.active { background: #ef4444; box-shadow: 0 0 8px #ef4444; animation: pulse-dot 1s infinite; }
@keyframes pulse-dot { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
#status-label { color: #9ca3af; font-size: 0.85rem; }
#hotkey-hint { margin-left: auto; color: #374151; font-size: 0.78rem; }
#hotkey-hint kbd {
    background: #1f2937; border: 1px solid #374151; border-radius: 4px;
    padding: 2px 6px; font-size: 0.78rem; color: #9ca3af;
}

/* ── Panels ── */
.wc-panel {
    background: #0d0d0d; border: 1px solid #1f2937;
    border-radius: 12px; padding: 16px;
}
.wc-panel-label {
    font-size: 0.7rem; font-weight: 600; letter-spacing: 1.5px;
    color: #4b5563; text-transform: uppercase; margin-bottom: 10px;
}

/* ── Transcript ── */
#transcript-box textarea {
    background: #000 !important; color: #d1d5db !important;
    border: none !important; font-size: 0.88rem !important;
    font-family: 'Menlo', monospace !important; resize: none !important;
    line-height: 1.6 !important;
}
#transcript-box label { display: none !important; }

/* ── Stress meter ── */
#stress-container { margin-bottom: 16px; }
#stress-bar-wrap {
    background: #1a1a1a; border-radius: 6px; height: 12px; overflow: hidden;
}
#stress-bar {
    height: 100%; width: 0%; border-radius: 6px;
    background: linear-gradient(90deg, #22c55e, #eab308, #ef4444);
    background-size: 300% 100%;
    transition: width 0.4s ease, background-position 0.4s ease;
}
#stress-value { font-size: 1.6rem; font-weight: 700; color: #fff; margin-bottom: 6px; }
#stress-label { font-size: 0.75rem; color: #6b7280; }

/* ── Emotion bars ── */
.emo-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.emo-name { width: 60px; font-size: 0.75rem; color: #6b7280; }
.emo-bar-bg { flex: 1; background: #1a1a1a; border-radius: 4px; height: 8px; overflow: hidden; }
.emo-bar-fill { height: 100%; border-radius: 4px; width: 0%; transition: width 0.4s ease; }
.emo-pct { width: 36px; text-align: right; font-size: 0.72rem; color: #6b7280; }

/* ── Coaching tip ── */
#tip-box {
    background: #0a0a1a; border: 1px solid #2e1065;
    border-left: 4px solid #7c3aed; border-radius: 10px;
    padding: 14px 16px; min-height: 60px;
}
#tip-box textarea {
    background: transparent !important; border: none !important;
    color: #c4b5fd !important; font-size: 0.92rem !important;
    font-family: 'Inter', sans-serif !important; resize: none !important;
    line-height: 1.6 !important;
}
#tip-box label { display: none !important; }

/* ── Buttons ── */
#start-btn { background: #16a34a !important; border: none !important; color: #fff !important; font-weight: 600 !important; }
#stop-btn  { background: #991b1b !important; border: none !important; color: #fff !important; font-weight: 600 !important; }
button.hidden-btn { display: none !important; }

/* ── Gradio overrides ── */
.block { background: transparent !important; border: none !important; padding: 0 !important; }
.svelte-1gfkn6j { background: transparent !important; }
footer { display: none !important; }
"""

HOTKEY_JS = """
() => {
    let vDown = false;
    const startBtn = document.querySelector('#hidden-start-btn button');
    const stopBtn  = document.querySelector('#hidden-stop-btn button');
    const dot      = document.getElementById('listen-dot');
    const lbl      = document.getElementById('status-label');

    document.addEventListener('keydown', (e) => {
        if (e.key === 'v' || e.key === 'V') {
            if (!vDown && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
                vDown = true;
                if (startBtn) startBtn.click();
                if (dot) dot.classList.add('active');
                if (lbl) lbl.textContent = '● LISTENING...';
            }
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.key === 'v' || e.key === 'V') {
            vDown = false;
        }
    });
}
"""

UPDATE_JS = """
(transcript, stress, emotions_json, tips) => {
    // Update stress bar
    const bar = document.getElementById('stress-bar');
    const val = document.getElementById('stress-value');
    const lbl = document.getElementById('stress-label');
    if (bar) {
        bar.style.width = stress + '%';
        bar.style.backgroundPosition = (100 - stress) + '% 0';
    }
    if (val) val.textContent = stress + '%';
    if (lbl) lbl.textContent = stress > 60 ? '🔴 HIGH STRESS' : stress > 30 ? '🟡 ELEVATED' : '🟢 CALM';

    // Update emotion bars
    if (emotions_json) {
        try {
            const emotions = JSON.parse(emotions_json);
            Object.entries(emotions).forEach(([name, score]) => {
                const fill = document.getElementById('emo-fill-' + name);
                const pct  = document.getElementById('emo-pct-' + name);
                if (fill) fill.style.width = (score * 100) + '%';
                if (pct)  pct.textContent = Math.round(score * 100) + '%';
            });
        } catch(e) {}
    }
}
"""


def _emotion_bars_html():
    colors = {
        "anger": "#ef4444", "fear": "#f97316",
        "disgust": "#a855f7", "sadness": "#3b82f6", "happiness": "#22c55e",
    }
    rows = ""
    for emo, color in colors.items():
        rows += f"""
        <div class="emo-row">
            <span class="emo-name">{emo}</span>
            <div class="emo-bar-bg">
                <div class="emo-bar-fill" id="emo-fill-{emo}" style="background:{color}"></div>
            </div>
            <span class="emo-pct" id="emo-pct-{emo}">0%</span>
        </div>"""
    return rows


def build_ui(ui_state: dict, on_start, on_stop):
    import json

    with gr.Blocks(title="WhisperCoach", css=CUSTOM_CSS) as demo:

        # ── Header ──────────────────────────────────────────────────────────
        gr.HTML("""
        <div id="wc-header">
            <h1>🎧 WhisperCoach</h1>
            <p>Real-time AI coaching for sales &amp; support reps</p>
        </div>
        """)

        # ── Status bar ──────────────────────────────────────────────────────
        gr.HTML("""
        <div id="status-bar">
            <div id="listen-dot"></div>
            <span id="status-label">Idle — hold V to listen</span>
            <span id="hotkey-hint">Push-to-talk: hold <kbd>V</kbd></span>
        </div>
        """)

        # ── Controls (visible) ──────────────────────────────────────────────
        with gr.Row():
            start_btn = gr.Button("▶ Start", elem_id="start-btn", scale=1)
            stop_btn  = gr.Button("■ Stop",  elem_id="stop-btn",  scale=1)
            status_out = gr.Textbox(value="Idle", label="", interactive=False, scale=4)

        # ── Hidden buttons for JS hotkey ─────────────────────────────────────
        hidden_start = gr.Button("_start", elem_id="hidden-start-btn", visible=False)
        hidden_stop  = gr.Button("_stop",  elem_id="hidden-stop-btn",  visible=False)

        # ── Main layout ──────────────────────────────────────────────────────
        with gr.Row(equal_height=True):

            # Left: transcript
            with gr.Column(scale=3):
                gr.HTML('<div class="wc-panel-label">📝 Live Transcript</div>')
                transcript_box = gr.Textbox(
                    lines=14, max_lines=14, interactive=False,
                    placeholder="Start listening to see transcript...",
                    elem_id="transcript-box",
                )

            # Right: stress + emotions
            with gr.Column(scale=2):
                gr.HTML(f"""
                <div class="wc-panel">
                    <div class="wc-panel-label">😤 Customer Stress</div>
                    <div id="stress-container">
                        <div id="stress-value">0%</div>
                        <div id="stress-bar-wrap"><div id="stress-bar"></div></div>
                        <div id="stress-label" style="margin-top:4px">🟢 CALM</div>
                    </div>
                    <br>
                    <div class="wc-panel-label">🧠 Emotions</div>
                    {_emotion_bars_html()}
                </div>
                """)

        # ── Coaching tip ─────────────────────────────────────────────────────
        gr.HTML('<div class="wc-panel-label" style="margin-top:16px">💡 Coaching Tip</div>')
        tips_box = gr.Textbox(
            lines=4, max_lines=4, interactive=False,
            placeholder="Coaching tips will appear here when stress is detected...",
            elem_id="tip-box",
        )

        # ── Hidden state for JS updates ──────────────────────────────────────
        emotions_json = gr.State("{}")

        # ── Poll timer ───────────────────────────────────────────────────────
        def poll():
            transcript = "\n".join(ui_state.get("transcript", [])[-20:])
            stress = ui_state.get("stress", 0)
            emotions = ui_state.get("emotions", {e: 0.0 for e in EMOTIONS})
            tips = "\n\n".join(ui_state.get("tips", []))
            emo_json = json.dumps(emotions)
            return transcript, stress, emo_json, tips

        timer = gr.Timer(value=0.4)
        timer.tick(fn=poll, outputs=[transcript_box, gr.State(), emotions_json, tips_box])

        # ── Stress + emotion visual update via JS ─────────────────────────────
        # We pass stress and emotions through a hidden number/textbox then run JS
        stress_hidden = gr.Number(value=0, visible=False)
        emo_hidden    = gr.Textbox(value="{}", visible=False)

        def poll_for_js():
            import json as _json
            stress = ui_state.get("stress", 0)
            emotions = ui_state.get("emotions", {e: 0.0 for e in EMOTIONS})
            transcript = "\n".join(ui_state.get("transcript", [])[-20:])
            tips = "\n\n".join(ui_state.get("tips", []))
            return transcript, stress, _json.dumps(emotions), tips

        timer2 = gr.Timer(value=0.4)
        timer2.tick(
            fn=poll_for_js,
            outputs=[transcript_box, stress_hidden, emo_hidden, tips_box],
        )

        stress_hidden.change(
            fn=None,
            inputs=[transcript_box, stress_hidden, emo_hidden, tips_box],
            js=UPDATE_JS,
        )

        # ── Button handlers ──────────────────────────────────────────────────
        def handle_start():
            on_start()
            return "● Listening..."

        def handle_stop():
            on_stop()
            return "■ Stopped"

        start_btn.click(fn=handle_start, outputs=status_out)
        stop_btn.click(fn=handle_stop, outputs=status_out)
        hidden_start.click(fn=handle_start, outputs=status_out)
        hidden_stop.click(fn=handle_stop, outputs=status_out)

        # ── V-key hotkey ─────────────────────────────────────────────────────
        demo.load(fn=None, js=HOTKEY_JS)

    return demo
