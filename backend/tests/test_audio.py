import io
import itertools

import av
import numpy as np
import pytest

from app.media.audio import RATE, AudioError, cut_points, decode, prepare


def tone(seconds: float, rate: int = 48_000, freq: float = 440.0, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * level * 32767).astype(np.int16)


def encode(samples: np.ndarray, fmt: str, codec: str, rate: int = 48_000) -> bytes:
    """Encode mono int16 samples the way a browser would, in memory."""
    buf = io.BytesIO()
    with av.open(buf, "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=rate, layout="mono")
        frame_size = stream.codec_context.frame_size or 1024
        for i in range(0, len(samples), frame_size):
            part = samples[i : i + frame_size].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(part, format="s16", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("fmt", "codec"),
    [("webm", "libopus"), ("mp4", "aac"), ("wav", "pcm_s16le"), ("mp3", "libmp3lame")],
)
def test_browser_formats_decode_to_16k_mono(fmt, codec):
    data = encode(tone(3.0), fmt, codec)
    samples = decode(data)
    assert samples.dtype == np.int16
    assert abs(len(samples) / RATE - 3.0) < 0.15  # codecs pad a little


def test_short_recording_is_one_chunk():
    (chunk,) = prepare(encode(tone(20.0), "webm", "libopus"))
    assert chunk.start_s == 0 and abs(chunk.end_s - 20.0) < 0.15
    assert chunk.wav[:4] == b"RIFF"


def test_long_recording_is_cut_at_the_quiet_moment():
    # 28 s speech-like tone, 0.6 s silence, 40 s tone: the cut should land in the silence.
    rate = RATE
    samples = np.concatenate([tone(28, rate), np.zeros(int(0.6 * rate), np.int16), tone(40, rate)])
    cuts = cut_points(samples)
    assert len(cuts) == 3  # two chunks: the 40 s left is under 1.5 x 30 s, so it stays whole
    assert 28.0 <= cuts[1] / rate <= 28.6


def test_chunks_cover_the_whole_recording_in_order():
    chunks = prepare(encode(tone(100.0), "mp4", "aac"))
    assert chunks[0].start_s == 0
    assert all(a.end_s == b.start_s for a, b in itertools.pairwise(chunks))
    assert abs(chunks[-1].end_s - 100.0) < 0.2
    assert all(15 <= c.end_s - c.start_s <= 50 for c in chunks)


def test_bad_audio_is_refused_with_a_clear_reason():
    with pytest.raises(AudioError, match="read"):
        prepare(b"this is not audio")
    with pytest.raises(AudioError, match="too short"):
        prepare(encode(tone(0.2), "wav", "pcm_s16le"))
    with pytest.raises(AudioError, match="limit"):
        prepare(encode(tone(5.0), "wav", "pcm_s16le"), max_seconds=2)
