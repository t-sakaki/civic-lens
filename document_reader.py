"""文書の読み上げ（Gemini TTS）: 議会の通告書・読み上げ原稿などを、確認用にAIの声で読み上げる。

画面の既定はブラウザの音声合成（無料・文面をどこにも送らない）。このモジュールは、ユーザーが
「自然な声」を選んだときだけ使う。費用の暴走を避けるため次の歯止めを持つ:
- 1回の文字数と分割数に上限（MAX_CHARS / MAX_CHUNKS）
- 既存の cost_guard（TTSの日次上限・IPごとのレート制限）に従う。上限に達したら画面側はブラウザの音声へ切り替える

最上位原則との関係:
- 冒頭で必ず「AIが作成した案で、議員本人の発言ではない。通告・登壇は議員自身が行う」ことを読み上げる
  （サーバー側で必ず付与し、画面側では外せない）
- 実在の人物の声に似せた音声は使わない（Geminiのプリセット音声のみ）
"""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import voice_panel

MAX_CHARS = 2400   # 1回に読み上げる本文の上限（日本語で約8分の読み上げに相当）
MAX_CHUNKS = 10    # TTS呼び出しの上限（免責1回を含む）
CHUNK_CHARS = 260  # 1回のTTS呼び出しで合成する長さの目安（文の区切りで分ける）
GAP_MS = 250
VOICE = "Kore"
NARRATOR_VOICE = voice_panel.NARRATOR_VOICE

KINDS = {
    "notice": "一般質問の通告書",
    "script": "登壇時の読み上げ原稿",
}


def disclaimer(kind: str) -> str:
    return (
        f"これはAIが作成した{KINDS[kind]}の案です。議員本人の発言ではありません。"
        "通告と登壇は議員自身が行います。確認用に読み上げます。"
    )


class DocumentTooLong(ValueError):
    """本文が MAX_CHARS を超えている"""


def split_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """文の区切り（。！？と改行）で、limit 文字前後のかたまりに分ける。"""
    sentences = [s for s in re.split(r"(?<=[。！？!?])|\n+", text or "") if s and s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) > limit:  # 句点のない長文は、読点・空白で分ける
            for piece in re.split(r"(?<=[、，,])|\s+", sentence):
                if current and len(current) + len(piece) > limit:
                    chunks.append(current)
                    current = ""
                current += piece
            continue
        if current and len(current) + len(sentence) > limit:
            chunks.append(current)
            current = ""
        current += sentence
    if current:
        chunks.append(current)
    return chunks


def prepare(text: str, kind: str) -> list[dict[str, str]]:
    """読み上げの台本: 先頭は必ず免責。本文が長すぎる場合は DocumentTooLong。"""
    if kind not in KINDS:
        raise ValueError(f"kind は {' / '.join(KINDS)} のいずれかを指定してください。")
    body = re.sub(r"[ \t　]+", " ", (text or "").strip())
    if not body:
        raise ValueError("読み上げる本文がありません。")
    if len(body) > MAX_CHARS:
        raise DocumentTooLong(f"本文が長すぎます（{MAX_CHARS}文字まで）。ブラウザの音声で読み上げてください。")
    chunks = split_text(body)
    if len(chunks) + 1 > MAX_CHUNKS:
        raise DocumentTooLong("本文の分割数が多すぎます。ブラウザの音声で読み上げてください。")
    return [{"voice": NARRATOR_VOICE, "text": disclaimer(kind)}] + [{"voice": VOICE, "text": c} for c in chunks]


_cache: dict[str, bytes] = {}
_CACHE_MAX = 20


def synthesize_document(client: Any, text: str, kind: str) -> bytes:
    """本文を合成してWAVを返す。失敗時は例外（画面側はブラウザの音声に切り替える）。"""
    key = hashlib.sha256(f"{kind}\n{text}".encode("utf-8")).hexdigest()
    cached = _cache.get(key)
    if cached:
        return cached
    script = prepare(text, kind)
    with ThreadPoolExecutor(max_workers=min(len(script), 5), thread_name_prefix="tts-doc") as pool:
        pcm = list(pool.map(lambda line: voice_panel._synthesize_line(client, line["voice"], line["text"]), script))
    wav = voice_panel._to_wav(pcm, gap_ms=GAP_MS)
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = wav
    return wav
