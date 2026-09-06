import pytest
from auth import (
    hash_password, verify_password,
    create_session_token, verify_session_token,
    register_user, authenticate_password, authenticate_wallet,
    generate_siwe_nonce
)


def test_password_hashing():
    password = "MySecurePassword123!"
    hashed = hash_password(password)
    assert hashed != password
    assert ":" in hashed
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_session_token():
    user_id = "usr-test-12345"
    token = create_session_token(user_id)
    assert token is not None
    assert "." in token

    verified_user_id = verify_session_token(token)
    assert verified_user_id == user_id

    # 改ざんトークン
    tampered_token = token[:-4] + "abcd"
    assert verify_session_token(tampered_token) is None


def test_user_registration_and_login():
    import uuid
    unique_user = f"citizen_{uuid.uuid4().hex[:6]}"
    password = "password123"
    email = f"{unique_user}@example.com"

    # 新規登録
    user = register_user(username=unique_user, password=password, email=email)
    assert user.username == unique_user
    assert user.email == email
    assert user.civic_id.startswith("市民#")

    # 正しいパスワードで認証
    auth_user = authenticate_password(unique_user, password)
    assert auth_user is not None
    assert auth_user.user_id == user.user_id

    # メールアドレスでもログイン可能
    auth_user_by_email = authenticate_password(email, password)
    assert auth_user_by_email is not None
    assert auth_user_by_email.user_id == user.user_id

    # 誤ったパスワード
    assert authenticate_password(unique_user, "wrongpass") is None


def test_web3_wallet_login():
    wallet = "0x71C836643F37740aB5635112437172771413847a"
    nonce = generate_siwe_nonce()
    assert len(nonce) >= 16

    user = authenticate_wallet(wallet_address=wallet, nonce=nonce)
    assert user.wallet_address.lower() == wallet.lower()
    assert user.civic_id.startswith("市民#")

    # 再度同じウォレットでログインした場合は同一ユーザーが返る
    user2 = authenticate_wallet(wallet_address=wallet)
    assert user2.user_id == user.user_id
