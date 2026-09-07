"""Civic Lens — ユーザー認証（Firebase Authentication + Firestore）

Firebase Authentication をID/パスワードの管理基盤として使用し、
Civic ID・ウォレット連携などのプロフィール情報は Firestore の
`users` コレクションに保存する。

Cloud Run のコンテナはリクエスト間でファイルシステムの内容を保持しないため、
以前のJSONファイル保存方式ではデプロイやスケールのたびにユーザーデータが失われていた。

メール/パスワード認証とMetaMask等のWeb3ウォレットによる
Sign-In with Ethereum (SIWE) の両方に対応。
"""

import os
import re
import time
import hmac
import json
import base64
import secrets
import hashlib
from typing import Optional
from datetime import datetime

import requests
from pydantic import BaseModel
from eth_account import Account
from eth_account.messages import encode_defunct
from firebase_admin import auth as firebase_auth

from firebase_client import get_firestore_client

FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")
SIGN_IN_WITH_PASSWORD_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "civic-lens-super-secret-key-2026")
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600  # 7日間有効
NONCE_EXPIRY_SECONDS = 5 * 60  # SIWE Nonceは5分間のみ有効（ワンタイム）

USERS_COLLECTION = "users"
NONCES_COLLECTION = "wallet_nonces"


class User(BaseModel):
    user_id: str
    username: str
    email: Optional[str] = None
    wallet_address: Optional[str] = None
    civic_id: str
    created_at: str
    last_login: str


def _users_ref():
    return get_firestore_client().collection(USERS_COLLECTION)


def _nonces_ref():
    return get_firestore_client().collection(NONCES_COLLECTION)


def _user_from_doc(data: dict) -> User:
    return User(**{field: data.get(field) for field in User.model_fields})


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower()) or secrets.token_hex(4)


def _synthetic_email(display_name: str) -> str:
    """Firebase Authenticationはメールアドレスを識別子として必須とするため、
    メール未入力のユーザー名のみ登録・ウォレット登録を内部専用ドメインで補う"""
    return f"{_slug(display_name)}-{secrets.token_hex(3)}@civic-lens.local"


def _new_civic_id() -> str:
    return f"市民#{secrets.randbelow(90000) + 10000}"


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 で安全にソルト付きハッシュ化（互換性維持用ユーティリティ）"""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key_hex = hashed.split(":", 1)
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
        return hmac.compare_digest(expected, key_hex)
    except Exception:
        return False


def create_session_token(user_id: str) -> str:
    """署名付きセッショントークンを生成（自己検証型・ストレージ不要）"""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    b64_payload = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{signature}"


def verify_session_token(token: str) -> Optional[str]:
    """セッショントークンを検証し user_id を返す"""
    if not token or "." not in token:
        return None
    try:
        b64_payload, signature = token.split(".", 1)
        expected = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None

        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))

        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload.get("sub")
    except Exception:
        return None


def register_user(username: str, password: str, email: Optional[str] = None) -> User:
    """新規ユーザー登録（Firebase Authenticationにアカウント作成 + Firestoreにプロフィール保存）"""
    clean_username = username.strip()
    if len(clean_username) < 2:
        raise ValueError("ユーザー名は2文字以上で入力してください")
    if len(password) < 6:
        raise ValueError("パスワードは6文字以上で入力してください")

    users_ref = _users_ref()
    if list(users_ref.where("username_lower", "==", clean_username.lower()).limit(1).stream()):
        raise ValueError("このユーザー名は既に使用されています")
    clean_email = email.strip().lower() if email else None
    if clean_email and list(users_ref.where("email", "==", clean_email).limit(1).stream()):
        raise ValueError("このメールアドレスは既に登録されています")

    auth_email = email.strip() if email else _synthetic_email(clean_username)
    try:
        firebase_user = firebase_auth.create_user(
            email=auth_email,
            password=password,
            display_name=clean_username,
        )
    except firebase_auth.EmailAlreadyExistsError:
        raise ValueError("このメールアドレスは既に登録されています")

    now_iso = datetime.utcnow().isoformat() + "Z"
    user = User(
        user_id=firebase_user.uid,
        username=clean_username,
        email=clean_email,
        wallet_address=None,
        civic_id=_new_civic_id(),
        created_at=now_iso,
        last_login=now_iso,
    )
    users_ref.document(firebase_user.uid).set({
        **user.model_dump(),
        "username_lower": clean_username.lower(),
        "auth_email": auth_email,
    })
    return user


def authenticate_password(username_or_email: str, password: str) -> Optional[User]:
    """ユーザー名/メールとパスワードで認証（Identity Toolkit REST APIでFirebase側の検証を実施）"""
    if not FIREBASE_WEB_API_KEY:
        raise RuntimeError("FIREBASE_WEB_API_KEY が設定されていません")

    query = username_or_email.strip().lower()
    users_ref = _users_ref()

    doc = None
    for candidate in (
        users_ref.where("username_lower", "==", query).limit(1),
        users_ref.where("email", "==", query).limit(1),
    ):
        results = list(candidate.stream())
        if results:
            doc = results[0]
            break
    if doc is None:
        return None

    data = doc.to_dict()
    auth_email = data.get("auth_email") or data.get("email")
    if not auth_email:
        return None

    resp = requests.post(
        SIGN_IN_WITH_PASSWORD_URL,
        params={"key": FIREBASE_WEB_API_KEY},
        json={"email": auth_email, "password": password, "returnSecureToken": False},
        timeout=10,
    )
    if resp.status_code != 200:
        return None

    now_iso = datetime.utcnow().isoformat() + "Z"
    users_ref.document(doc.id).update({"last_login": now_iso})
    data["last_login"] = now_iso
    return _user_from_doc(data)


def generate_siwe_nonce() -> str:
    """Sign-In with Ethereum 用のランダムなワンタイム Nonce を生成し、Firestoreに記録する

    ここで発行・保存した Nonce のみが authenticate_wallet() で消費可能。
    未記録のNonceを送りつけられても検証を通過できないようにし、リプレイ攻撃を防ぐ。
    """
    nonce = secrets.token_hex(16)
    _nonces_ref().document(nonce).set({"expires_at": time.time() + NONCE_EXPIRY_SECONDS})
    return nonce


def _consume_nonce(nonce: str) -> bool:
    """Nonceが有効（発行済み・未使用・期限内）であれば消費してTrueを返す"""
    if not nonce:
        return False
    doc_ref = _nonces_ref().document(nonce)
    snap = doc_ref.get()
    if not snap.exists:
        return False
    doc_ref.delete()
    expires_at = snap.to_dict().get("expires_at", 0)
    return expires_at >= time.time()


def build_siwe_message(wallet_address: str, nonce: str) -> str:
    """ウォレットが署名するSIWEメッセージを組み立てる（フロントと文言を完全一致させること）"""
    return (
        "Civic Lens にサインインします。\n"
        "このリクエストは送金やトランザクションの承認ではありません。\n\n"
        f"Wallet: {wallet_address}\n"
        f"Nonce: {nonce}"
    )


def authenticate_wallet(wallet_address: str, signature: Optional[str] = None, nonce: Optional[str] = None) -> User:
    """Web3ウォレットアドレスによるログイン・自動アカウント作成（SIWE署名検証必須）"""
    if not signature or not nonce:
        raise ValueError("署名（signature）とNonce（nonce）が必要です")

    if not _consume_nonce(nonce):
        raise ValueError("Nonceが無効か、有効期限（5分）が切れています。もう一度お試しください")

    message = build_siwe_message(wallet_address, nonce)
    try:
        recovered_address = Account.recover_message(
            encode_defunct(text=message), signature=signature
        )
    except Exception:
        raise ValueError("署名の検証に失敗しました")

    if recovered_address.lower() != wallet_address.strip().lower():
        raise ValueError("署名がウォレットアドレスと一致しません")

    clean_wallet = wallet_address.strip().lower()
    users_ref = _users_ref()
    now_iso = datetime.utcnow().isoformat() + "Z"

    existing = list(users_ref.where("wallet_address", "==", clean_wallet).limit(1).stream())
    if existing:
        doc = existing[0]
        users_ref.document(doc.id).update({"last_login": now_iso})
        data = doc.to_dict()
        data["last_login"] = now_iso
        return _user_from_doc(data)

    # 初回ログイン時はウォレット連携ユーザーを自動作成
    display_name = f"Wallet {clean_wallet[:6]}...{clean_wallet[-4:]}"
    auth_email = _synthetic_email(display_name)
    firebase_user = firebase_auth.create_user(email=auth_email, display_name=display_name)

    user = User(
        user_id=firebase_user.uid,
        username=display_name,
        email=None,
        wallet_address=clean_wallet,
        civic_id=_new_civic_id(),
        created_at=now_iso,
        last_login=now_iso,
    )
    users_ref.document(firebase_user.uid).set({
        **user.model_dump(),
        "username_lower": display_name.lower(),
        "auth_email": auth_email,
    })
    return user


def get_user_by_id(user_id: str) -> Optional[User]:
    """IDからユーザーを取得"""
    doc = _users_ref().document(user_id).get()
    if not doc.exists:
        return None
    return _user_from_doc(doc.to_dict())
