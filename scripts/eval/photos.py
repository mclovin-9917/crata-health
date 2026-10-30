"""Phone-photo versions of the eval documents, made in memory (nothing is written to disk).

`run_eval.py --photos` reads every case through these instead of the clean renders, to answer
"does it still work on the photos people actually take?" Each image is laid on a desk-coloured
background, tilted and turned a little, given a soft shadow from one side, darkened, blurred
slightly and saved as a phone-quality JPEG. The random choices are seeded by the case id and the
file name, so every run (and every model) sees exactly the same photo.
"""

from __future__ import annotations

import hashlib
import io
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _rng(case_id: str, name: str) -> random.Random:
    seed = int(hashlib.sha256(f"{case_id}/{name}".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def phone_photo(png: bytes, case_id: str, name: str) -> bytes:
    """A clean PNG render in, a JPEG that looks like a quick phone photo of the paper out."""
    rng = _rng(case_id, name)
    page = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = page.size

    # The paper on a desk, with a margin to move around in.
    pad = int(max(w, h) * 0.12)
    desk = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (118, 98, 80))
    desk.paste(page, (pad, pad))

    # Held at a slant: a small perspective skew, then a small turn.
    W, H = desk.size
    d = lambda: rng.uniform(0.0, 0.045)
    quad = (W * d(), H * d(), W * d(), H * (1 - d()), W * (1 - d()), H * (1 - d()), W * (1 - d()), H * d())
    desk = desk.transform((W, H), Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)
    desk = desk.rotate(rng.uniform(-4.0, 4.0), resample=Image.Resampling.BICUBIC,
                       expand=False, fillcolor=(118, 98, 80))

    # A soft shadow falling across from one side (the phone or a hand).
    shade = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(shade)
    x0 = int(W * rng.uniform(0.55, 0.8))
    draw.polygon([(x0, 0), (W, 0), (W, H), (int(x0 - W * 0.15), H)], fill=int(rng.uniform(70, 110)))
    shade = shade.filter(ImageFilter.GaussianBlur(W * 0.04))
    desk = Image.composite(Image.new("RGB", (W, H), (20, 20, 20)), desk, shade)

    # Indoor light and a slightly soft focus.
    desk = ImageEnhance.Brightness(desk).enhance(rng.uniform(0.78, 0.9))
    desk = ImageEnhance.Contrast(desk).enhance(rng.uniform(0.8, 0.92))
    desk = desk.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.1)))

    out = io.BytesIO()
    desk.save(out, "JPEG", quality=int(rng.uniform(55, 68)))
    return out.getvalue()


if __name__ == "__main__":  # write one example next to this file to look at: python photos.py A1
    import sys
    from pathlib import Path

    case = sys.argv[1] if len(sys.argv) > 1 else "A1"
    src = Path(__file__).resolve().parents[2] / "samples" / "eval" / case
    for n in ("prescription.png", "bill.png"):
        Path(sys.argv[2] if len(sys.argv) > 2 else ".", f"{case}-{n[:-4]}-photo.jpg").write_bytes(
            phone_photo((src / n).read_bytes(), case, n))
