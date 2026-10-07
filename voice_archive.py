"""音声アーカイブ: 音声パネルの音声を常に記録し、人が承認したものだけを公開する。

方針（AGENTS.md の最上位原則から）:
- 記録は開示請求の有無にかかわらず常に残す。ただし既定は非公開（status="private"）
- 公開は人（運営者）が1件ずつ確認して承認する（"published"）。AIの自動公開はしない
- 公開できる音声は、冒頭で「AI生成のフィクション」と読み上げているもの（voice_panel.OPENING）だけ
- 公開時にもガードレールを再検査し、違反が残っていれば公開を拒否する
- 各スナップショットは不変（台本のハッシュごとにID）。あとで再分析されても、公開済みの内容は変わらない
- 出典（ニュースURL）・生成日時・モデル・音声と台本のハッシュを一緒に残し、「いつ誰が何を言ったことにしたか」を検証可能にする

保存先:
  音声本体 : Cloud Storage（VOICE_ARCHIVE_BUCKET）。未設定なら VOICE_ARCHIVE_DIR（既定 /tmp/civic_lens_voice_archive）
  メタデータ: Firestore（voice_archive）。認証情報のない開発環境のみローカルJSON
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import guardrail
import voice_panel
from storage_backend import use_firestore

COLLECTION = "voice_archive"
PRIVATE = "private"
PUBLISHED = "published"

_LOCK = threading.Lock()
_DIR = Path(os.getenv("VOICE_ARCHIVE_DIR") or "/tmp/civic_lens_voice_archive")


class NotConfirmed(ValueError):
    """運営者が内容を確認した旨（confirmed）が無いまま公開しようとした"""


class GuardrailViolation(ValueError):
    def __init__(self, violations: list[str]):
        super().__init__(f"ガードレールに抵触する内容が残っています: {', '.join(violations)}")
        self.violations = violations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def script_hash(script: list[dict[str, str]]) -> str:
    return _sha256("\n".join(f"{l['speaker']}\t{l['text']}" for l in script))


def archive_id(news_id: str, text_hash: str) -> str:
    return f"{news_id}-{text_hash[:8]}"


# ---- メタデータ（Firestore / ローカルJSON） ----

def _index_path() -> Path:
    return _DIR / "index.json"


def _load_index() -> dict[str, Any]:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _put(entry: dict[str, Any]) -> None:
    if use_firestore():
        from firebase_client import get_firestore_client

        get_firestore_client().collection(COLLECTION).document(entry["archive_id"]).set(entry)
        return
    _DIR.mkdir(parents=True, exist_ok=True)
    data = _load_index()
    data[entry["archive_id"]] = entry
    _index_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get(aid: str) -> Optional[dict[str, Any]]:
    if use_firestore():
        from firebase_client import get_firestore_client

        snap = get_firestore_client().collection(COLLECTION).document(aid).get()
        return snap.to_dict() if snap.exists else None
    return _load_index().get(aid)


def _all() -> list[dict[str, Any]]:
    if use_firestore():
        from firebase_client import get_firestore_client

        entries = [d.to_dict() for d in get_firestore_client().collection(COLLECTION).stream()]
    else:
        entries = list(_load_index().values())
    return sorted(entries, key=lambda e: e.get("generated_at", ""), reverse=True)


# ---- 音声本体（Cloud Storage / ローカル） ----

def _bucket_name() -> str:
    return os.getenv("VOICE_ARCHIVE_BUCKET", "").strip()


def _store_audio(aid: str, wav: bytes) -> str:
    bucket = _bucket_name()
    if bucket:
        from google.cloud import storage

        storage.Client().bucket(bucket).blob(f"voices/{aid}.wav").upload_from_string(wav, content_type="audio/wav")
        return f"gs://{bucket}/voices/{aid}.wav"
    _DIR.mkdir(parents=True, exist_ok=True)
    (_DIR / f"{aid}.wav").write_bytes(wav)
    return f"local:{aid}.wav"


def read_audio(entry: dict[str, Any]) -> Optional[bytes]:
    ref = entry.get("audio_ref", "")
    if ref.startswith("gs://"):
        from google.cloud import storage

        bucket, _, name = ref[5:].partition("/")
        blob = storage.Client().bucket(bucket).blob(name)
        return blob.download_as_bytes() if blob.exists() else None
    if ref.startswith("local:"):
        path = _DIR / ref[6:]
        return path.read_bytes() if path.exists() else None
    return None


# ---- 記録・承認 ----

def save(news_id: str, record: dict[str, Any], wav: bytes) -> dict[str, Any]:
    """音声パネルの音声を記録する（非公開）。同じ台本なら再保存せず既存の記録を返す"""
    script = voice_panel.build_script(record)
    aid = archive_id(news_id, script_hash(script))
    with _LOCK:
        existing = get(aid)
        if existing:
            return existing
        src = record.get("source_news") or {}
        entry = {
            "archive_id": aid,
            "news_id": news_id,
            "source_news": {"title": src.get("title"), "link": src.get("link"), "published": src.get("published")},
            "region": record.get("region"),
            "anger_level": record.get("overall_anger_level"),
            "summary": record.get("summary"),
            "script": [{"speaker": l["speaker"], "text": l["text"]} for l in script],
            "text_sha256": script_hash(script),
            "audio_sha256": _sha256(wav),
            "audio_bytes": len(wav),
            "audio_ref": _store_audio(aid, wav),
            "tts_model": voice_panel._tts_model(),
            "disclaimer": voice_panel.OPENING,
            "generated_at": _now(),
            "status": PRIVATE,
            "published_at": None,
        }
        _put(entry)
        return entry


def publish(aid: str, confirmed: bool) -> dict[str, Any]:
    """運営者の承認で公開する。内容確認（confirmed）とガードレール再検査を通ったものだけ"""
    if not confirmed:
        raise NotConfirmed("出典と照らして内容（事実の断定がないこと等）を確認した旨のチェックが必要です")
    with _LOCK:
        entry = get(aid)
        if entry is None:
            raise KeyError(aid)
        violations: list[str] = []
        for line in entry["script"]:
            if line["text"] == voice_panel.OPENING:
                continue  # 固定の免責文（「実在の市民の声ではなく…」）は、なりすまし検出に反応するので対象外
            for kind in guardrail.check_text(line["text"]):
                if kind not in violations:
                    violations.append(kind)
        if violations:
            raise GuardrailViolation(violations)
        entry = {**entry, "status": PUBLISHED, "published_at": _now()}
        _put(entry)
        return entry


def unpublish(aid: str) -> dict[str, Any]:
    with _LOCK:
        entry = get(aid)
        if entry is None:
            raise KeyError(aid)
        entry = {**entry, "status": PRIVATE, "published_at": None}
        _put(entry)
        return entry


def list_published(limit: int = 50) -> list[dict[str, Any]]:
    return [e for e in _all() if e.get("status") == PUBLISHED][:limit]


def list_all(limit: int = 200) -> list[dict[str, Any]]:
    return _all()[:limit]


def public_view(entry: dict[str, Any]) -> dict[str, Any]:
    """公開APIに出す項目だけ（audio_ref等の内部情報は出さない）"""
    return {
        "archive_id": entry["archive_id"],
        "source_news": entry.get("source_news"),
        "region": entry.get("region"),
        "summary": entry.get("summary"),
        "script": entry["script"],
        "disclaimer": entry.get("disclaimer"),
        "generated_at": entry.get("generated_at"),
        "published_at": entry.get("published_at"),
        "audio_sha256": entry.get("audio_sha256"),
        "text_sha256": entry.get("text_sha256"),
        "tts_model": entry.get("tts_model"),
    }
