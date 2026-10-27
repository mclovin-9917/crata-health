"""Audio preparation, all in memory: any browser format in, 16 kHz mono WAV chunks out.

Browsers record webm/opus (Chrome, Firefox) or mp4/aac (Safari); Omni is tested with WAV.
PyAV (ffmpeg's libraries) decodes from a byte buffer, so nothing is written to disk.

Long recordings are cut into chunks of about 30 s, at the quietest moment near each cut, so
words are rarely split and every transcript piece has reliable start and end times.
"""

from __future__ import annotations

import io
import itertools
import wave
from dataclasses import dataclass

import av
import numpy as np

RATE = 16_000
FRAME_S = 0.1  # loudness is measured in 100 ms frames when looking for a quiet cut


class AudioError(ValueError):
    """The upload is not usable audio (unreadable, empty or too long)."""


@dataclass
class AudioChunk:
    start_s: float
    end_s: float
    wav: bytes


def too_long(seconds: float | None, max_seconds: float) -> AudioError:
    """The error for a recording over the limit (`seconds` None: stopped before the end)."""
    if max_seconds < 120:
        heard = f"is {seconds:.0f} s" if seconds is not None else "is too long"
        return AudioError(f"The recording {heard}; the limit is {max_seconds:.0f} s")
    heard = f"is {seconds / 60:.0f} min" if seconds is not None else "is too long"
    return AudioError(f"The recording {heard}; the limit is {max_seconds / 60:.0f} min")


def decode(data: bytes, max_seconds: float | None = None) -> np.ndarray:
    """Any container/codec ffmpeg knows -> 16 kHz mono int16 samples.

    With `max_seconds`, a recording over it is refused as soon as that is known: from the
    container's own length when it has one, else as soon as decoding passes the limit, so a long
    low-bitrate file is never decoded in full.
    """
    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise AudioError("The file has no audio track")
            if max_seconds is not None and container.duration:
                known = container.duration / av.time_base
                if known > max_seconds + 1:  # a second of slack for container rounding
                    raise too_long(known, max_seconds)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            parts: list[np.ndarray] = []
            total = 0
            for frame in container.decode(container.streams.audio[0]):
                for f in resampler.resample(frame):
                    arr = f.to_ndarray().reshape(-1)
                    total += len(arr)
                    parts.append(arr)
                if max_seconds is not None and total > (max_seconds + 1) * RATE:
                    raise too_long(None, max_seconds)
            parts += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]  # flush
    except av.FFmpegError as exc:
        raise AudioError(f"Could not read the audio: {exc}") from exc
    return np.concatenate(parts).astype(np.int16) if parts else np.zeros(0, np.int16)


def to_wav(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def cut_points(samples: np.ndarray, target_s: float = 30.0, search_s: float = 5.0) -> list[int]:
    """Sample indexes where chunks start and end (first is 0, last is the length).

    Each cut goes at the quietest 100 ms frame within `search_s` of the target length. A tail
    shorter than half a chunk is merged into the previous chunk.
    """
    n = len(samples)
    frame = int(RATE * FRAME_S)
    if n <= int(RATE * target_s * 1.5):
        return [0, n]
    frames = samples[: n - n % frame].astype(np.float32).reshape(-1, frame)
    loudness = np.sqrt((frames**2).mean(axis=1))
    cuts = [0]
    while n - cuts[-1] > RATE * target_s * 1.5:
        lo = int((cuts[-1] / RATE + target_s - search_s) / FRAME_S)
        hi = int((cuts[-1] / RATE + target_s + search_s) / FRAME_S)
        quietest = lo + int(np.argmin(loudness[lo:hi]))
        cuts.append(quietest * frame + frame // 2)
    return [*cuts, n]


def prepare(data: bytes, max_seconds: float = 600.0, target_s: float = 30.0) -> list[AudioChunk]:
    samples = decode(data, max_seconds)
    seconds = len(samples) / RATE
    if seconds < 0.5:
        raise AudioError("The recording is empty or too short")
    if seconds > max_seconds:
        raise too_long(seconds, max_seconds)
    cuts = cut_points(samples, target_s)
    return [
        AudioChunk(start_s=a / RATE, end_s=b / RATE, wav=to_wav(samples[a:b]))
        for a, b in itertools.pairwise(cuts)
    ]
