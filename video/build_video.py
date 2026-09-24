"""Render the Guardrail Lab promo video.

    python video/capture.py            # 1. screenshots of the live dashboard
    python video/build_video.py        # 2. narration → timeline → frames → MP4

Output: video/build/guardrail_lab_promo.mp4 (1920×1080, 30 fps, H.264 + AAC)
Narration uses the macOS `say` voice (VOICE env var); pass --silent for a captions-only version to narrate yourself.
"""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
BUILD = HERE / "build"
AUDIO = BUILD / "audio"
FPS = 30
VOICE = os.environ.get("VOICE", "Samantha")
RATE = os.environ.get("RATE", "178")
LEAD, TAIL = 0.45, 0.85  # seconds of silence before/after each scene's narration

# (scene id, narration). Spelling tweaks are for the TTS voice only; captions show the clean text.
SCENES = [
    ("hook", "Every AI app has the same problem. The model does whatever the prompt tells it to."),
    ("problem", "Ask a raw model to reveal its hidden instructions, and it will. Personal data goes straight to the model. "
                "And nothing checks what comes back out."),
    ("intro", "Meet Guardrail Lab. A multi-stage guardrail pipeline that controls what goes into a language model, "
              "and what comes out."),
    ("pipeline", "Every request is normalized to decode hidden tricks. Rules and an LLM classifier sort it into seven categories. "
                 "A policy engine decides whether to allow, rewrite, clarify, redirect, or block. "
                 "Then the answer is generated, validated against a strict schema, and repaired if it breaks."),
    ("d_safe", "Here it is, live. A normal computer science question passes every check. Green across the board, "
               "and a clean, validated answer."),
    ("d_block", "Now, an attack. Ignore all previous instructions. The rules catch it instantly, so the pipeline skips "
                "the classifier entirely, and blocks it in milliseconds."),
    ("d_transform", "Smarter attacks hide inside real tasks. Guardrail Lab strips the injected instruction, re-checks the "
                    "rewritten prompt, and still delivers the summary."),
    ("d_grandma", "Some jailbreaks have no trigger words at all. The rules miss this one, but the LLM classifier catches it. "
                  "That's why the layers matter."),
    ("d_unsafe", "Harmful requests are refused, but never with a dead end. The user gets a genuinely helpful, safe alternative."),
    ("d_pii", "Personal data is redacted before any model sees it. It's restored locally in the final answer, "
              "and the logs only ever store the redacted version."),
    ("d_repair", "On the way out, every response must match a strict schema. When the output breaks, the validator catches it, "
                 "and the repair loop fixes it automatically."),
    ("d_canary", "And if the model ever leaks its hidden prompt, a secret canary token gives it away, and the answer is withheld."),
    ("d_compare", "Side by side, the difference is clear. The unguarded baseline leaks its secret instructions. "
                  "With guardrails, the same attack is blocked."),
    ("results", "And it's measured, not just demoed. On a held-out test set, prompt injection leaks dropped from two in seven to zero. "
                "Personal data sent to the model dropped from one hundred percent to zero. With zero wrongly refused requests, "
                "and a faster median response, because blocked requests never reach the model."),
    ("classifier", "The input classifier scores a macro F1 of zero point nine two, picks the right action ninety eight percent "
                   "of the time, and every mistake it made still ended safely."),
    ("stack", "And it all runs on a laptop. Python, FastAPI, Streamlit, Pydantic, SQLite, and a local Qwen 3 model through Ollama."),
    ("outro", "Guardrail Lab. Detect, classify, transform, generate, validate, and repair. The code is open source on GitHub."),
]
TTS_FIXES = {"LLM": "L L M", "Qwen 3": "Kwen 3", "FastAPI": "Fast A P I", "SQLite": "S Q Lite", "F1": "F 1",
             "Pydantic": "Pie-dantic", "normalized": "normalized"}
CAPTION_FIXES = {"Qwen 3": "Qwen3"}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out)


def make_audio(silent: bool) -> dict:
    AUDIO.mkdir(parents=True, exist_ok=True)
    timeline, t = {}, 0.0
    for sid, text in SCENES:
        spoken = text
        for k, v in TTS_FIXES.items():
            spoken = spoken.replace(k, v)
        wav = AUDIO / f"{sid}.aiff"
        if not wav.exists() or (AUDIO / f"{sid}.txt").read_text() != spoken + VOICE + RATE:
            subprocess.run(["say", "-v", VOICE, "-r", RATE, "-o", str(wav), spoken], check=True)
            (AUDIO / f"{sid}.txt").write_text(spoken + VOICE + RATE)
        d = duration(wav)
        caption = text
        for k, v in CAPTION_FIXES.items():
            caption = caption.replace(k, v)
        dur = LEAD + d + TAIL
        timeline[sid] = {"start": round(t, 3), "dur": round(dur, 3), "lead": LEAD, "audio": round(d, 3),
                         "sentences": sentences(caption), "wav": str(wav)}
        t += dur
    return {"total": round(t, 3), "scenes": timeline}


def mix_audio(tl: dict, out: Path) -> None:
    inputs, filters = [], []
    for i, (sid, sc) in enumerate(tl["scenes"].items()):
        inputs += ["-i", sc["wav"]]
        ms = int((sc["start"] + sc["lead"]) * 1000)
        filters.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms},apad[a{i}]")
    n = len(tl["scenes"])
    graph = ";".join(filters) + ";" + "".join(f"[a{i}]" for i in range(n)) + \
        f"amix=inputs={n}:normalize=0:duration=longest,atrim=0:{tl['total']},volume=1.6,alimiter=limit=0.95[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
                    "-ac", "2", str(out)], check=True)


def content_heights(boxes: dict) -> None:
    """Measure where each screenshot's content ends so the camera never pans into blank page."""
    from PIL import Image
    for name, meta in boxes.items():
        img = Image.open(BUILD / "shots" / meta["file"]).convert("L")
        w, h = img.size
        scale = w / 1440
        px = img.load()
        bottom = h - 1
        while bottom > 0 and all(abs(px[x, bottom] - px[w - 10, h - 5]) < 6 for x in range(int(w * 0.3), w - 20, 12)):
            bottom -= 4
        meta["contentH"] = round(bottom / scale + 40)


def render_frames(tl: dict, boxes: dict, out: Path, preview_at: list[float] | None = None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto((HERE / "promo.html").as_uri())
        page.wait_for_function("window.READY === true")
        page.evaluate("([tl, bx]) => { window.TIMELINE = tl; window.BOXES = bx; }", [tl, boxes])
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(1500)  # images + web fonts

        if preview_at:
            for t in preview_at:
                page.evaluate(f"window.render({t})")
                page.screenshot(path=str(BUILD / f"preview_{t:06.2f}.png"))
            browser.close()
            return

        n = int(tl["total"] * FPS)
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
                               "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
        t0 = time.time()
        for i in range(n):
            page.evaluate(f"window.render({i / FPS})")
            ff.stdin.write(page.screenshot(type="jpeg", quality=93))
            if i % 150 == 0:
                el = time.time() - t0
                print(f"frame {i}/{n}  ({el:.0f}s elapsed, ~{el / max(i, 1) * (n - i):.0f}s left)", flush=True)
        ff.stdin.close()
        ff.wait()
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", nargs="*", type=float, help="render still frames at these times (seconds) instead")
    ap.add_argument("--silent", action="store_true", help="no narration track (captions only)")
    args = ap.parse_args()

    tl = make_audio(args.silent)
    boxes = json.loads((BUILD / "shots" / "boxes.json").read_text())
    content_heights(boxes)
    (BUILD / "timeline.json").write_text(json.dumps(tl, indent=1))
    print(f"timeline: {tl['total']:.1f}s, {len(tl['scenes'])} scenes")
    for sid, sc in tl["scenes"].items():
        print(f"  {sc['start']:6.1f}s  {sid:<12} {sc['dur']:5.1f}s")

    if args.preview is not None:
        times = args.preview or [sc["start"] + sc["dur"] * 0.7 for sc in tl["scenes"].values()]
        render_frames(tl, boxes, BUILD / "unused.mp4", preview_at=times)
        print("previews written to", BUILD)
        return

    video = BUILD / "video_only.mp4"
    render_frames(tl, boxes, video)
    final = BUILD / ("guardrail_lab_promo_silent.mp4" if args.silent else "guardrail_lab_promo.mp4")
    if args.silent:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c", "copy", str(final)], check=True)
    else:
        audio = BUILD / "narration.wav"
        mix_audio(tl, audio)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", str(final)], check=True)
    print("done:", final, f"({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
