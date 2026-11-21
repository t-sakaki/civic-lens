"""Trusted local MCP publication workflow."""
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
_pending = {}
_lock = threading.Lock()
NOTICE = "公開文面は削除できません。存在証明は内容の真実性や行政への提出完了を証明しません。AIは情報提供・書式作成支援を行います。"

def prepare_publication(kind: str, content: str, public_summary: str, authority: str, legal_basis: str) -> dict:
    """Prepare a preview without sending. Present it to the user before publishing."""
    from web3_attestation import scan_personal_info, normalize_requested_documents, MAX_REQUESTED_DOCUMENTS_BYTES
    types = {"disclosure": "行政文書開示請求", "appeal": "審査請求"}
    if kind not in types or not all(x.strip() for x in (content, public_summary, authority, legal_basis)):
        raise ValueError("種別・本文・公開概要・機関・根拠を確認してください")
    summary = normalize_requested_documents(public_summary)
    if len(summary.encode()) > MAX_REQUESTED_DOCUMENTS_BYTES:
        raise ValueError("公開概要が長すぎます")
    warnings = scan_personal_info(" ".join((summary, authority, legal_basis)))
    if warnings:
        return {"ready": False, "personal_info_warnings": warnings}
    payload = dict(content=content, requested_documents=summary, authority=authority,
                   legal_basis=legal_basis, request_type=types[kind], title=types[kind],
                   record_id="mcp-" + uuid.uuid4().hex)
    config = {k: os.getenv(k, "") for k in ("EAS_SCHEMA_UID", "CHAIN_RPC_URL", "EAS_CHAIN_ID")}
    preview = {k: v for k, v in payload.items() if k != "content"}
    preview.update(document_hash="0x"+hashlib.sha256(content.strip().encode()).hexdigest(),
                   notice=NOTICE, chain_id=config["EAS_CHAIN_ID"] or "84532",
                   schema_uid=config["EAS_SCHEMA_UID"],
                   fee="ガス代は代理署名ウォレットが負担します。")
    digest = hashlib.sha256(json.dumps(preview, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    token = secrets.token_urlsafe(32)
    with _lock:
        for key in list(_pending):
            if _pending[key]["expires"] < time.time():
                del _pending[key]
        if len(_pending) >= 100:
            raise ValueError("保留中のプレビューが多すぎます")
        _pending[token] = dict(payload=payload, config=config, digest=digest, expires=time.time()+600)
    return dict(ready=True, preview=preview, preview_digest=digest, confirmation_token=token, expires_in_seconds=600)

def publish_record(confirmation_token: str, preview_digest: str, user_confirmed: bool = False) -> dict:
    """Irreversible: only call after explicit user approval of the exact preview. Never auto-retry."""
    from web3_attestation import issue_attestation
    if not user_confirmed:
        raise ValueError("公開プレビューに対する本人の明示的な確認が必要です")
    with _lock:
        entry = _pending.get(confirmation_token)
        if not entry or entry["expires"] < time.time():
            raise ValueError("期限切れ、または実行済みです")
        if not secrets.compare_digest(entry["digest"], preview_digest):
            raise ValueError("プレビューが一致しません")
        if any(os.getenv(k, "") != v for k, v in entry["config"].items()):
            raise ValueError("設定変更後は再プレビューしてください")
        del _pending[confirmation_token]
    result = issue_attestation(**entry["payload"], publish_plaintext=True)
    return {"attestation": result.model_dump(), "notice": NOTICE}
