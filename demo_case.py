"""デモケースのエクスポート: ピッチで再現したいニュース1件分の記録と音声を demo/cases/ に台本化して保存する。

12/1 最終ピッチの5分デモのための準備機能。ニュース・SDGsエージェントの声（怒り再現）・
開示請求対象の提案・音声（Gemini TTSの議論パネル）を1つのケースとして書き出し、
当日はネットワーク障害やTTS日次上限（cost_guard）に左右されずに再現できるようにする。

方針（AGENTS.md の最上位原則から）:
- 擬似市民の声は免責文（pseudo_citizen_voice_disclaimer）つきのまま記録ごと書き出す
- 音声は冒頭で「AI生成のフィクション」を読み上げているもの（voice_archive の記録）だけを出す
- 出典（ニュースURL・発行日）を記録ごと保持し、実在の報道との対応を検証可能にする

書き出されるファイル（--out で指定。既定 demo/cases/）:
  <news_id>.json  記録（news_anger_records 1件分）+ 音声のメタデータ（台本・SHA-256・発話区間）
  <news_id>.opus  議論音声（voice_archive に保存済みのもの。無い場合は --with-audio で生成できる）

CLI: scripts/export_demo_case.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import audio_codec
import news_reactions
import voice_archive
import voice_panel

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "demo" / "cases"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def latest_voice_entry(news_id: str) -> Optional[Dict[str, Any]]:
    """この記録の議論音声のうち、音声が読める最新の voice_archive 記録を返す（無ければNone）"""
    entries = [e for e in voice_archive.list_all(limit=500) if e.get("news_id") == news_id]
    for entry in entries:  # 新しい順に並んでいる
        if voice_archive.audio_exists(entry):
            return entry
    return None


def build_case_payload(news_id: str) -> Dict[str, Any]:
    """記録1件と、対応する音声のメタデータから、ケースのペイロードを組み立てる"""
    record = news_reactions.get_record(news_id)
    if record is None:
        raise KeyError(news_id)
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": news_id,
        "exported_at": _now(),
        "region": record.get("region"),
        "title": (record.get("source_news") or {}).get("title"),
        "disclaimer": record.get("pseudo_citizen_voice_disclaimer"),
        "record": record,
        "voice": None,
    }
    entry = latest_voice_entry(news_id)
    if entry is None:
        return payload
    stored = voice_archive.read_audio(entry, "opus")
    if stored is None:
        return payload
    audio = stored[0]
    # 話者ハイライト用の発話区間は、WAVに戻して長さを測れば再計算できる（画面と音声を一致させる）
    timing = voice_panel.panel_timing(record, audio_codec.opus_to_wav(audio))
    payload["voice"] = {
        "archive_id": entry["archive_id"],
        "filename": f"{news_id}.opus",
        "audio_codec": entry.get("audio_codec", "opus"),
        "audio_sha256": entry.get("audio_sha256"),
        "audio_bytes": entry.get("audio_bytes"),
        "tts_model": entry.get("tts_model"),
        "text_sha256": entry.get("text_sha256"),
        "script": entry.get("script"),
        "timing": timing,
        "disclaimer": entry.get("disclaimer"),
        "generated_at": entry.get("generated_at"),
        "status": entry.get("status"),
    }
    return payload


def export_case(
    news_id: str,
    out_dir: Optional[Path] = None,
    synthesize: bool = False,
    client: Any = None,
) -> Dict[str, Any]:
    """デモケースを書き出す。戻り値は {"json": パス, "audio": パス or None, "payload": 中身}。

    synthesize=True: voice_archive に音声が無いとき、Gemini TTSで議論音声を生成してから
    保存する（ピッチのリハーサル前に一度だけ実行する想定。cost_guard の日次上限を消費する）。
    """
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    if synthesize and latest_voice_entry(news_id) is None:
        _synthesize_into_archive(news_id, client)
    payload = build_case_payload(news_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{news_id}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audio_path: Optional[Path] = None
    if payload["voice"] is not None:
        entry = voice_archive.get(payload["voice"]["archive_id"])
        if entry is not None:
            stored = voice_archive.read_audio(entry, "opus")
            if stored is not None:
                audio_path = out_dir / payload["voice"]["filename"]
                audio_path.write_bytes(stored[0])
    return {"json": json_path, "audio": audio_path, "payload": payload}


def _synthesize_into_archive(news_id: str, client: Any = None) -> None:
    """音声が未生成のケースで --with-audio を指定されたとき、Gemini TTSで議論音声を生成して voice_archive に保存する"""
    record = news_reactions.get_record(news_id)
    if record is None:
        raise KeyError(news_id)
    if not record.get("voices"):
        return  # 音声にできる声がまだ無い（未分析の記録）。記録だけを書き出す
    if client is None:
        import agent

        client = agent.get_agent().genai_client
    if client is None:
        raise RuntimeError("Gemini クライアントが未設定のため音声を生成できません（GEMINI_API_KEY）")
    wav = voice_panel.synthesize_panel(client, news_id, record)
    voice_archive.save(news_id, record, wav)


def list_cases(limit: int = 100) -> List[Dict[str, Any]]:
    """ピッチ候補を選ぶための記録一覧（新しい順）"""
    with_audio = {
        e.get("news_id")
        for e in voice_archive.list_all(limit=500)
        if voice_archive.audio_exists(e)
    }
    items: List[Dict[str, Any]] = []
    for r in news_reactions.list_records(limit=limit):
        src = r.get("source_news") or {}
        items.append({
            "news_id": r.get("news_id"),
            "title": src.get("title"),
            "region": r.get("region"),
            "overall_anger_level": r.get("overall_anger_level"),
            "summary": r.get("summary"),
            "has_summary": bool(src.get("summary")),
            "voices": len(r.get("voices") or []),
            "proposals": len(r.get("proposals") or []),
            "has_request_detail": bool(r.get("anger_analysis")),
            "has_audio": r.get("news_id") in with_audio,
            "updated_at": r.get("updated_at"),
        })
    return items
