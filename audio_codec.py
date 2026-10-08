"""音声の圧縮: TTSのWAV(24kHz/16bit/モノラル) ⇄ Opus(Ogg)。

アーカイブはOpus 24kbpsで保存する（話し声でWAVの約1/16。90秒で約270KB）。Firestoreの無料枠に
収めるため（バケット等の課金リソースを増やさない）。Safari等でOpusを再生できない閲覧者向けに、
配信時にWAVへ戻す関数も持つ。エンコードは PyAV（ffmpeg内蔵）を使い、メモリ上で完結する。
"""
from __future__ import annotations

import io
import wave

import av

OPUS_BITRATE = 24_000
OPUS_RATE = 48_000  # Opusが扱うサンプルレート（24kHzのTTS出力はここへリサンプルされる）
OPUS_MIME = 'audio/ogg; codecs=opus'
WAV_MIME = "audio/wav"


def wav_to_opus(wav: bytes) -> bytes:
    out = io.BytesIO()
    with av.open(io.BytesIO(wav)) as src, av.open(out, "w", format="ogg") as dst:
        stream = dst.add_stream("libopus", rate=OPUS_RATE)
        stream.bit_rate = OPUS_BITRATE
        stream.layout = "mono"
        resampler = av.AudioResampler(format=stream.codec_context.format, layout="mono", rate=OPUS_RATE)
        for frame in src.decode(audio=0):
            for f in resampler.resample(frame):
                dst.mux(stream.encode(f))
        for f in resampler.resample(None):
            dst.mux(stream.encode(f))
        dst.mux(stream.encode(None))
    return out.getvalue()


def opus_to_wav(opus: bytes) -> bytes:
    pcm = bytearray()
    with av.open(io.BytesIO(opus)) as src:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24_000)
        frames = list(src.decode(audio=0)) + [None]
        for frame in frames:
            for f in resampler.resample(frame):
                pcm += bytes(f.planes[0])[: f.samples * 2]  # 行末のパディングを除く
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(bytes(pcm))
    return buf.getvalue()
