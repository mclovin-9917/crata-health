"""Smoke test: does a provider accept text, an image and audio, and which model IDs does it list?

Run from the repo root (after `pip install -e "backend[dev]"` and setting NVIDIA_API_KEY and/or
NEBIUS_API_KEY in backend/.env):

    python scripts/smoke_test.py --provider nvidia --list-models
    python scripts/smoke_test.py --provider nvidia --audio path/to/consult.wav
    python scripts/smoke_test.py --provider nebius --audio path/to/consult.wav

--provider picks the reason provider. Perceive (images, audio) always runs on NVIDIA build,
because Nano Omni is not on Token Factory (tested 2026-10-12).

Status: text, image and audio checked on NVIDIA build (2026-10-11); text on Token Factory
(2026-10-12).
"""

import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai.client import AIClient, AIError, audio_part, image_part
from app.config import Settings

MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


def sample_image() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    draw.text((20, 40), "Rx: Amoxicillin 500 mg, 3 times a day, 5 days", fill="black", font=font)
    draw.text((20, 110), "Allergy noted: Penicillin", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def step(label: str, fn, limit: int | None = 400) -> None:
    start = time.time()
    try:
        out = fn()
        print(f"[OK]   {label}  ({time.time() - start:.1f}s)\n       {str(out)[:limit]!r}\n")
    except (AIError, Exception) as exc:  # noqa: BLE001
        # Cut long errors: some echo the whole base64 request back.
        print(f"[FAIL] {label}  ({time.time() - start:.1f}s)\n       {str(exc)[:500]}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["nvidia", "nebius"], required=True)
    ap.add_argument("--audio", help="path to a short consult recording")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    settings = Settings(reason_provider=args.provider)
    client = AIClient(settings)
    for step_name, ep in (("perceive", settings.perceive), ("reason", settings.reason)):
        print(f"{step_name}: {ep.provider} {ep.base_url} {ep.model}")
    print()

    if args.list_models:
        def models():
            ids = sorted(m.id for m in client.reason_client.models.list().data)
            return [i for i in ids if "nemotron" in i.lower()] or ids[:20]

        step("list models (nemotron)", models, limit=None)

    step("reason: text", lambda: client.reason("Answer in one short sentence.", "Name one use of amoxicillin."))
    step(
        "perceive: image (prescription)",
        lambda: client.perceive([image_part(sample_image())], "Read all text in this image exactly."),
    )
    if args.audio:
        mime = MIME.get(os.path.splitext(args.audio)[1].lower(), "audio/wav")
        with open(args.audio, "rb") as f:
            data = f.read()
        step(
            f"perceive: audio ({args.audio})",
            lambda: client.perceive([audio_part(data, mime)], "Transcribe this audio exactly."),
        )
    else:
        print("(no --audio given: audio not tested)")


if __name__ == "__main__":
    main()
