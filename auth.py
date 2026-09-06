"""Civic Lens — ユーザー認証（ハイブリッド：メール/PW ＆ Web3ウォレット SIWE）

メールアドレス・パスワードによる標準Web認証と、
MetaMask等のWeb3ウォレットによるSign-In with Ethereum (SIWE) の両方に対応。
非公開請求の保護、マイページ請求履歴管理、Civic IDの一元化を実現。
"""

import os
import json
import time
import hmac
import hashlib
import secrets
import base64
from typing import Optional, Dict, List
from datetime import datetime
from pydantic import BaseModel
from eth_account import Account
from eth_account.messages import encode_defunct

DEFAULT_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "data")
if os.getenv("VERCEL"):
    STORAGE_DIR = "/tmp/civic_lens_data"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    USER_STORAGE_PATH = os.path.join(STORAGE_DIR, "users.json")
    SESSION_STORAGE_PATH = os.path.join(STORAGE_DIR, "sessions.json")
    NONCE_STORAGE_PATH = os.path.join(STORAGE_DIR, "wallet_nonces.json")
else:
    USER_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "users.json")
    SESSION_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "sessions.json")
    NONCE_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "wallet_nonces.json")

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "civic-lens-super-secret-key-2026")
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600  # 7日間有効
NONCE_EXPIRY_SECONDS = 5 * 60  # SIWE Nonceは5分間のみ有効（ワンタイム）


class User(BaseModel):
    user_id: str
    username: str
    email: Optional[str] = None
    password_hash: Optional[str] = None
    wallet_address: Optional[str] = None
    civic_id: str
    created_at: str
    last_login: str


def _ensure_storage():
    os.makedirs(os.path.dirname(USER_STORAGE_PATH), exist_ok=True)
    if not os.path.exists(USER_STORAGE_PATH):
        with open(USER_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
    if not os.path.exists(SESSION_STORAGE_PATH):
        with open(SESSION_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
    if not os.path.exists(NONCE_STORAGE_PATH):
        with open(NONCE_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_users() -> Dict[str, dict]:
    _ensure_storage()
    try:
        with open(USER_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_users(users: Dict[str, dict]):
    _ensure_storage()
    with open(USER_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _load_sessions() -> Dict[str, dict]:
    _ensure_storage()
    try:
        with open(SESSION_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_sessions(sessions: Dict[str, dict]):
    _ensure_storage()
    with open(SESSION_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def _load_nonces() -> Dict[str, float]:
    _ensure_storage()
    try:
        with open(NONCE_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_nonces(nonces: Dict[str, float]):
    _ensure_storage()
    with open(NONCE_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(nonces, f)


def _consume_nonce(nonce: str) -> bool:
    """Nonceが有効（発行済み・未使用・期限内）であれば消費してTrueを返す"""
    if not nonce:
        return False
    nonces = _load_nonces()
    expires_at = nonces.pop(nonce, None)

    # 期限切れNonceを掃除しておく（ストアの肥大化防止）
    now = time.time()
    nonces = {n: exp for n, exp in nonces.items() if exp > now}
    _save_nonces(nonces)

    if expires_at is None or expires_at < now:
        return False
    return True


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 で安全にソルト付きハッシュ化"""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """パスワードの検証"""
    try:
        salt, key_hex = hashed.split(":", 1)
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
        return hmac.compare_digest(expected, key_hex)
    except Exception:
        return False


def create_session_token(user_id: str) -> str:
    """署名付きセッショントークンを生成"""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
        "nonce": secrets.token_hex(8)
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    b64_payload = base64.urlsafe_b64encode(payload_json.encode('utf-8')).decode('utf-8').rstrip('=')
    signature = hmac.new(SECRET_KEY.encode('utf-8'), b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    token = f"{b64_payload}.{signature}"

    # セッションキャッシュに登録
    sessions = _load_sessions()
    sessions[token] = {"user_id": user_id, "exp": payload["exp"]}
    _save_sessions(sessions)

    return token


def verify_session_token(token: str) -> Optional[str]:
    """セッショントークンを検証し user_id を返す"""
    if not token or "." not in token:
        return None
    try:
        b64_payload, signature = token.split(".", 1)
        expected = hmac.new(SECRET_KEY.encode('utf-8'), b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None

        # パディング補正してデコード
        padded = b64_payload + '=' * (-len(b64_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode('utf-8')).decode('utf-8'))
        
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload.get("sub")
    except Exception:
        return None


def register_user(username: str, password: str, email: Optional[str] = None) -> User:
    """新規ユーザー登録（パスワード認証）"""
    users = _load_users()

    clean_username = username.strip()
    if len(clean_username) < 2:
        raise ValueError("ユーザー名は2文字以上で入力してください")
    if len(password) < 6:
        raise ValueError("パスワードは6文字以上で入力してください")

    # ユーザー名・メールアドレスの重複確認
    for u in users.values():
        if u["username"].lower() == clean_username.lower():
            raise ValueError("このユーザー名は既に使用されています")
        if email and u.get("email") and u["email"].lower() == email.strip().lower():
            raise ValueError("このメールアドレスは既に登録されています")

    user_id = f"usr-{secrets.token_hex(6)}"
    civic_id = f"市民#{secrets.randbelow(90000) + 10000}"
    now_iso = datetime.utcnow().isoformat() + "Z"

    user = User(
        user_id=user_id,
        username=clean_username,
        email=email.strip() if email else None,
        password_hash=hash_password(password),
        wallet_address=None,
        civic_id=civic_id,
        created_at=now_iso,
        last_login=now_iso
    )

    users[user_id] = user.model_dump()
    _save_users(users)
    return user


def authenticate_password(username_or_email: str, password: str) -> Optional[User]:
    """ユーザー名/メールとパスワードで認証"""
    users = _load_users()
    query = username_or_email.strip().lower()

    for u_data in users.values():
        u_name = u_data.get("username", "").lower()
        u_mail = (u_data.get("email") or "").lower()
        if query == u_name or (u_mail and query == u_mail):
            if u_data.get("password_hash") and verify_password(password, u_data["password_hash"]):
                u_data["last_login"] = datetime.utcnow().isoformat() + "Z"
                users[u_data["user_id"]] = u_data
                _save_users(users)
                return User(**u_data)
    return None


def generate_siwe_nonce() -> str:
    """Sign-In with Ethereum 用のランダムなワンタイム Nonce を生成し、サーバー側に記録する

    ここで発行・保存した Nonce のみが authenticate_wallet() で消費可能。
    未記録のNonceを送りつけられても検証を通過できないようにし、リプレイ攻撃を防ぐ。
    """
    nonce = secrets.token_hex(16)
    nonces = _load_nonces()
    nonces[nonce] = time.time() + NONCE_EXPIRY_SECONDS
    _save_nonces(nonces)
    return nonce


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

    users = _load_users()
    clean_wallet = wallet_address.strip().lower()

    # 既存のウォレットユーザーを検索
    for u_data in users.values():
        if u_data.get("wallet_address") and u_data["wallet_address"].lower() == clean_wallet:
            u_data["last_login"] = datetime.utcnow().isoformat() + "Z"
            users[u_data["user_id"]] = u_data
            _save_users(users)
            return User(**u_data)

    # 初回ログイン時はウォレット連携ユーザーを自動作成
    user_id = f"usr-w3-{secrets.token_hex(6)}"
    civic_id = f"市民#{secrets.randbelow(90000) + 10000}"
    display_name = f"Wallet {clean_wallet[:6]}...{clean_wallet[-4:]}"
    now_iso = datetime.utcnow().isoformat() + "Z"

    user = User(
        user_id=user_id,
        username=display_name,
        email=None,
        password_hash=None,
        wallet_address=clean_wallet,
        civic_id=civic_id,
        created_at=now_iso,
        last_login=now_iso
    )

    users[user_id] = user.model_dump()
    _save_users(users)
    return user


def get_user_by_id(user_id: str) -> Optional[User]:
    """IDからユーザーを取得"""
    users = _load_users()
    if user_id in users:
        return User(**users[user_id])
    return None
