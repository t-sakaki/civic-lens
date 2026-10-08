"""音声アーカイブ: 音声パネルの音声を常に記録し、人が承認したものだけを公開する。

方針（AGENTS.md の最上位原則から）:
- 記録は開示請求の有無にかかわらず常に残す。ただし既定は非公開（status="private"）
- 公開は人（運営者）が1件ずつ確認して承認する（"published"）。AIの自動公開はしない
- 公開できる音声は、冒頭で「AI生成のフィクション」と読み上げているもの（voice_panel.OPENING）だけ
- 公開時にもガードレールを再検査し、違反が残っていれば公開を拒否する
- 各スナップショットは不変（台本のハッシュごとにID）。あとで再分析されても、公開済みの内容は変わらない
- 出典（ニュースURL）・生成日時・モデル・音声と台本のハッシュを一緒に残し、「いつ誰が何を言ったことにしたか」を検証可能にする

保存先（課金リソースを増やさないため、バケットは使わず Firestore の無料枠に収める）:
  音声本体 : Opus 24kbps（audio_codec.py）にして Firestore の voice_archive_audio にチャンク分割して保存
             （90秒で約270KB。1ドキュメント1MiBの上限を超える長さは分割）
  メタデータ: Firestore の voice_archive
  認証情報のない開発環境のみ、どちらもローカル（VOICE_ARCHIVE_DIR。既定 /tmp/civic_lens_voice_archive）
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import audio_codec
import guardrail
import voice_panel
from storage_backend import use_firestore

COLLECTION = "voice_archive"
AUDIO_COLLECTION = "voice_archive_audio"
CHUNK_BYTES = 700_000  # Firestoreの1ドキュメントは1MiBまで。余裕を見て分割する
PRIVATE = "private"
PUBLISHED = "published"

_LOCK = threading.Lock()
_DIR = Path(os.getenv("VOICE_ARCHIVE_DIR") or "/tmp/civic_lens_voice_archive")


class NotConfirmed(ValueError):
    """運営者が内容を確認した旨（confirmed）が無いまま公開しようとした"""


class AudioMissing(ValueError):
    """音声ファイルが見つからない記録は公開できない（公開ページが壊れる）"""


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


def slug(entry: dict[str, Any]) -> str:
    """ダウンロード時のファイル名（拡張子なし）。ASCIIのみ・日付＋記録IDで、記録と1対1に辿れる

    例: civic-lens-20261007-9440b1b73024677e-2f2e1a3c
    日本語の記事タイトルは文字化け・衝突しやすいのでファイル名には使わない。
    """
    date = (entry.get("generated_at") or "")[:10].replace("-", "")
    return "-".join(p for p in ("civic-lens", date, entry["archive_id"]) if p)


def download_headers(name: str, ext: str = "opus") -> dict[str, str]:
    """再生はそのまま、保存時のファイル名だけをスラッグにする"""
    return {"Content-Disposition": f'inline; filename="{name}.{ext}"', "X-Voice-Filename": f"{name}.{ext}"}


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


# ---- 音声本体（Firestore / ローカル） ----

def split_chunks(data: bytes, size: int = CHUNK_BYTES) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]


def _store_audio(aid: str, audio: bytes, ext: str) -> str:
    if use_firestore():
        from firebase_client import get_firestore_client

        db = get_firestore_client()
        batch = db.batch()
        for seq, chunk in enumerate(split_chunks(audio)):
            batch.set(db.collection(AUDIO_COLLECTION).document(f"{aid}_{seq:03d}"), {"archive_id": aid, "seq": seq, "data": chunk})
        batch.commit()
        return f"firestore:{aid}"
    _DIR.mkdir(parents=True, exist_ok=True)
    (_DIR / f"{aid}.{ext}").write_bytes(audio)
    return f"local:{aid}.{ext}"


def _read_stored(entry: dict[str, Any]) -> Optional[bytes]:
    ref = entry.get("audio_ref", "")
    if ref.startswith("firestore:"):
        from firebase_client import get_firestore_client

        aid = ref[len("firestore:"):]
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = get_firestore_client().collection(AUDIO_COLLECTION).where(filter=FieldFilter("archive_id", "==", aid)).stream()
        chunks = sorted((d.to_dict() for d in docs), key=lambda c: c["seq"])
        return b"".join(bytes(c["data"]) for c in chunks) if chunks else None
    if ref.startswith("local:"):
        path = _DIR / ref[6:]
        return path.read_bytes() if path.exists() else None
    return None


def read_audio(entry: dict[str, Any], fmt: Optional[str] = None) -> Optional[tuple[bytes, str, str]]:
    """保存した音声を (バイト列, MIME, 拡張子) で返す。fmt="wav" なら、Opusを再生できない端末向けにWAVへ戻す"""
    audio = _read_stored(entry)
    if audio is None:
        return None
    stored = entry.get("audio_codec", "wav")
    if stored == "opus" and fmt == "wav":
        return audio_codec.opus_to_wav(audio), audio_codec.WAV_MIME, "wav"
    if stored == "opus":
        return audio, audio_codec.OPUS_MIME, "opus"
    return audio, audio_codec.WAV_MIME, "wav"


# ---- 記録・承認 ----

def save(news_id: str, record: dict[str, Any], wav: bytes) -> dict[str, Any]:
    """音声パネルの音声（WAV）をOpusに圧縮して記録する（非公開）。同じ台本なら再保存せず既存の記録を返す"""
    script = voice_panel.build_script(record)
    aid = archive_id(news_id, script_hash(script))
    with _LOCK:
        existing = get(aid)
        if existing and _read_stored(existing) is not None:
            return existing
        # 記録はあるのに音声が無い（一時領域に保存していた旧版の記録など）場合は、音声を保存し直して復旧する
        src = record.get("source_news") or {}
        audio = audio_codec.wav_to_opus(wav)
        entry = {
            "archive_id": aid,
            "news_id": news_id,
            "source_news": {"title": src.get("title"), "link": src.get("link"), "published": src.get("published")},
            "region": record.get("region"),
            "anger_level": record.get("overall_anger_level"),
            "summary": record.get("summary"),
            "script": [{"speaker": l["speaker"], "text": l["text"]} for l in script],
            "text_sha256": script_hash(script),
            "audio_sha256": _sha256(audio),  # 保存した（配信する）Opusファイルのハッシュ
            "audio_bytes": len(audio),
            "audio_codec": "opus",
            "audio_ref": _store_audio(aid, audio, "opus"),
            "tts_model": voice_panel._tts_model(),
            "disclaimer": voice_panel.OPENING,
            "generated_at": _now(),
            "status": existing["status"] if existing else PRIVATE,
            "published_at": existing["published_at"] if existing else None,
        }
        if existing:
            entry["generated_at"] = existing["generated_at"]
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
        if _read_stored(entry) is None:
            raise AudioMissing("音声ファイルが見つかりません。ニュースの「音声で聴く」で音声を作り直すと復旧します")
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
        "audio_codec": entry.get("audio_codec", "wav"),
        "text_sha256": entry.get("text_sha256"),
        "tts_model": entry.get("tts_model"),
    }
