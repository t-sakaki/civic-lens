"""audio_codec: TTSのWAV ⇄ Opus（Gemini通信なし）"""
import io
import math
import struct
import wave

import audio_codec


def _tone_wav(seconds: float = 2.0, rate: int = 24000) -> bytes:
    frames = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def test_opus_is_much_smaller_and_round_trips_with_same_duration():
    wav = _tone_wav()
    opus = audio_codec.wav_to_opus(wav)
    assert opus[:4] == b"OggS" and len(opus) < len(wav) / 8  # 24kbpsなのでWAVの1/8以下
    back = audio_codec.opus_to_wav(opus)
    w = wave.open(io.BytesIO(back))
    assert w.getframerate() == 24000 and w.getnchannels() == 1
    assert abs(w.getnframes() / w.getframerate() - 2.0) < 0.1  # 長さが保たれる
