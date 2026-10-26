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


def decode(data: bytes) -> np.ndarray:
    """Any container/codec ffmpeg knows -> 16 kHz mono int16 samples."""
    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise AudioError("The file has no audio track")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            parts: list[np.ndarray] = []
            for frame in container.decode(container.streams.audio[0]):
                parts += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
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
    samples = decode(data)
    seconds = len(samples) / RATE
    if seconds < 0.5:
        raise AudioError("The recording is empty or too short")
    if seconds > max_seconds:
        raise AudioError(f"The recording is {seconds / 60:.0f} min; the limit is "
                         f"{max_seconds / 60:.0f} min")
    cuts = cut_points(samples, target_s)
    return [
        AudioChunk(start_s=a / RATE, end_s=b / RATE, wav=to_wav(samples[a:b]))
        for a, b in itertools.pairwise(cuts)
    ]
