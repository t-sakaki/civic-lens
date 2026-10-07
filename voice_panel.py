"""音声パネル: 名乗り出たSDGsエージェントたちの議論を、エージェントごとに違う声で読み上げる。

Gemini TTS のマルチスピーカーは2話者までのため、1行（1話者）ずつ合成して WAV に連結する。
最上位原則との関係:
- 冒頭で必ず「AIが生成したフィクションで、実在の市民の声ではない」ことを読み上げる
- 各エージェントは「AIの○○担当」と名乗る（実在の個人に聞こえる口調・名前を使わない）
- 読み上げる本文は guardrail 通過後の声のみ
費用: 1パネルあたり最大 MAX_AGENT_LINES + 2 回のTTS呼び出し。cost_guard の TTS_DAILY_CALL_LIMIT で上限管理し、結果はニュースごとにキャッシュする。
"""
from __future__ import annotations

import io
import os
import re
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from google.genai import types  # type: ignore

import cost_guard
from timeout_utils import call_with_timeout

TTS_MODEL_DEFAULT = "gemini-2.5-flash-preview-tts"
SAMPLE_RATE = 24000  # Gemini TTS は 24kHz / 16bit / モノラルの生PCMを返す
MAX_AGENT_LINES = 3
MAX_LINE_CHARS = 140
NARRATOR_VOICE = "Zephyr"
INTEGRATOR_VOICE = "Charon"
AGENT_VOICES = ["Kore", "Puck", "Leda", "Orus", "Aoede", "Fenrir"]

OPENING = "これは、AIエージェントたちによる議論です。実在の市民の声ではなく、AIが生成したフィクションです。"

_cache: dict[str, bytes] = {}
_CACHE_MAX = 30


def _short_label(label: str) -> str:
    return re.sub(r"^目標\d+\s*", "", str(label or "").strip()) or "行政監視"


def _clip(text: str, limit: int = MAX_LINE_CHARS) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    # 文の途中で切れないよう、収まる最後の文末（。！？）で切る（短くなりすぎる場合だけ「…」で切る）
    head = text[:limit]
    cut = max(head.rfind(c) for c in "。！？!?")
    if cut >= limit // 3:
        return text[: cut + 1]
    return text[: limit - 1] + "…"


def agent_lines(record: dict[str, Any]) -> list[dict[str, str]]:
    """名乗り出たエージェントごとの短い一言（怒りの強い順・最大 MAX_AGENT_LINES 体）。

    音声パネルの読み上げと、画面の表示の両方でこの内容を使い、画面と音声を一致させる。
    [{"theme", "label", "body"}]（label は「目標N」を除いた担当名）
    """
    lines = []
    voices = sorted(record.get("voices") or [], key=lambda v: -int(v.get("anger_level") or 0))
    for v in voices[:MAX_AGENT_LINES]:
        label = _short_label(v.get("label", ""))
        prefix_len = len(f"AIの{label}担当です。")
        body = _clip(v.get("pseudo_citizen_voice") or v.get("remark") or "", MAX_LINE_CHARS - prefix_len)
        if body:
            lines.append({"theme": v.get("theme", ""), "label": label, "body": body})
    return lines


def build_script(record: dict[str, Any]) -> list[dict[str, str]]:
    """記録から読み上げ台本を作る: [{"speaker", "voice", "text"}]（先頭は必ず免責の読み上げ）"""
    script = [{"speaker": "ナレーション", "voice": NARRATOR_VOICE, "text": OPENING}]
    for i, line in enumerate(agent_lines(record)):
        script.append({
            "speaker": f"AI・{line['label']}",
            "voice": AGENT_VOICES[i % len(AGENT_VOICES)],
            "text": f"AIの{line['label']}担当です。{line['body']}",
        })
    summary = record.get("summary")
    if summary:
        proposals = record.get("proposals") or []
        tail = f"開示請求の対象は、{proposals[0].get('target_authority')}が候補です。" if proposals and proposals[0].get("target_authority") else ""
        script.append({
            "speaker": "AI・統合エージェント",
            "voice": INTEGRATOR_VOICE,
            "text": _clip(f"統合エージェントの所見です。{summary}{tail}判断はあなた自身が行ってください。"),
        })
    return script


def _tts_model() -> str:
    return os.getenv("GEMINI_TTS_MODEL") or TTS_MODEL_DEFAULT


def _synthesize_line(client: Any, voice: str, text: str) -> bytes:
    cost_guard.consume("tts")
    response = call_with_timeout(
        client.models.generate_content,
        timeout_s=30.0,
        model=_tts_model(),
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    return response.candidates[0].content.parts[0].inline_data.data


def _to_wav(pcm_chunks: list[bytes], gap_ms: int = 350) -> bytes:
    gap = b"\x00\x00" * int(SAMPLE_RATE * gap_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(gap.join(pcm_chunks))
    return buf.getvalue()


def synthesize_panel(client: Any, news_id: str, record: dict[str, Any]) -> bytes:
    """台本を話者別に並行合成してWAVを返す。失敗時は例外（呼び出し側はテキスト表示のみに戻す）"""
    cached = _cache.get(news_id)
    if cached:
        return cached
    script = build_script(record)
    with ThreadPoolExecutor(max_workers=len(script), thread_name_prefix="tts") as pool:
        chunks = list(pool.map(lambda line: _synthesize_line(client, line["voice"], line["text"]), script))
    wav = _to_wav(chunks)
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[news_id] = wav
    return wav
