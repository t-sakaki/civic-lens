"""音声パネル: 名乗り出たSDGsエージェントたちの議論を、エージェントごとに違う声で読み上げる。

Gemini TTS のマルチスピーカーは2話者までのため、1行（1話者）ずつ合成して WAV に連結する。
最上位原則との関係:
- 冒頭で必ず「AIが生成したフィクションで、実在の市民の声ではない」ことを読み上げる
- 各エージェントは「AIの○○担当」と名乗る（実在の個人に聞こえる口調・名前を使わない）
- 読み上げる本文は guardrail 通過後の声のみ
費用: 1パネルあたり最大 1(免責) + MAX_AGENT_LINES + 1(所見) + MAX_PROPOSAL_LINES 回のTTS呼び出し（最大8回）。cost_guard の TTS_DAILY_CALL_LIMIT で上限管理し、結果はニュースごとにキャッシュする。
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
# 統合エージェントは、所見に加えて「開示請求の対象の機関と、請求する文書」を読み上げる。これがCivic Lensの成果物
MAX_PROPOSAL_LINES = 3  # 提案は機関ごとに1件（news_anger_agent.MAX_PROPOSALS と同じ）
MAX_DOCS_SPOKEN = 5  # 1提案で読み上げる文書の数（残りは「ほか◯件」）。全文書は画面に表示される
INTEGRATOR_LINE_CHARS = 360
CLOSING = "請求するかどうかの判断はあなた自身が行ってください。"
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


def _spoken_documents(docs: list[str]) -> str:
    """請求する文書を「1つ目、〇〇。2つ目、〇〇。」と番号つきで読み上げる文にする（多い分は「ほか◯件」）"""
    shown = [d.strip().rstrip("。") for d in docs[:MAX_DOCS_SPOKEN] if d and d.strip()]
    text = "".join(f"{i}つ目、{d}。" for i, d in enumerate(shown, 1))
    rest = len(docs) - len(shown)
    return text + (f"ほか{rest}件。" if rest > 0 else "")


def integrator_lines(record: dict[str, Any]) -> list[dict[str, str]]:
    """統合エージェントの読み上げ: 所見 → 提案ごと（請求先の機関と、請求する文書）。最後に本人の判断を促す。

    [{"speaker", "text"}]。開示請求の対象と文書の読み上げが、このパネルの核心。
    """
    parts: list[tuple[str, str]] = []
    summary = record.get("summary")
    if summary:
        level = record.get("overall_anger_level")
        anger = f"怒りのレベルは、10段階中{int(level)}です。" if isinstance(level, (int, float)) else ""
        parts.append(("AI・統合エージェント", f"統合エージェントの所見です。{summary}{anger}"))
    for i, p in enumerate((record.get("proposals") or [])[:MAX_PROPOSAL_LINES], 1):
        docs = [d for d in (p.get("documents") or []) if d]
        if not (p.get("target_authority") and docs):
            continue
        supporters = len(p.get("supporting_themes") or [])
        support = f"{supporters}体のエージェントが、この提案を支持しています。" if supporters else ""
        parts.append((
            f"AI・統合エージェント（提案{i}）",
            f"提案{i}。開示請求の請求先は、{p['target_authority']}です。{support}請求する文書は、{_spoken_documents(docs)}",
        ))
    lines = []
    for n, (speaker, text) in enumerate(parts):
        last = n == len(parts) - 1
        limit = INTEGRATOR_LINE_CHARS - (len(CLOSING) if last else 0)
        lines.append({"speaker": speaker, "text": _clip(text, limit) + (CLOSING if last else "")})
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
    for line in integrator_lines(record):
        script.append({"speaker": line["speaker"], "voice": INTEGRATOR_VOICE, "text": line["text"]})
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
