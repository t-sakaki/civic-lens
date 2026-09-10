import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from auth import (
    hash_password, verify_password,
    create_session_token, verify_session_token,
    register_user, authenticate_password, authenticate_wallet,
    generate_siwe_nonce, build_siwe_message,
    request_magic_link, verify_magic_link, _magic_links_ref, _hash_magic_token,
)


def _sign_login(private_key: str, wallet_address: str, nonce: str) -> str:
    message = build_siwe_message(wallet_address, nonce)
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    return signed.signature.hex()


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
    account = Account.create()
    wallet = account.address
    nonce = generate_siwe_nonce()
    assert len(nonce) >= 16

    signature = _sign_login(account.key, wallet, nonce)
    user = authenticate_wallet(wallet_address=wallet, signature=signature, nonce=nonce)
    assert user.wallet_address.lower() == wallet.lower()
    assert user.civic_id.startswith("市民#")

    # 再度同じウォレットでログインした場合は同一ユーザーが返る（新しいNonce・署名で）
    nonce2 = generate_siwe_nonce()
    signature2 = _sign_login(account.key, wallet, nonce2)
    user2 = authenticate_wallet(wallet_address=wallet, signature=signature2, nonce=nonce2)
    assert user2.user_id == user.user_id


def test_web3_wallet_login_requires_signature_and_nonce():
    wallet = Account.create().address
    with pytest.raises(ValueError):
        authenticate_wallet(wallet_address=wallet)


def test_web3_wallet_login_rejects_signature_from_other_wallet():
    """他人の秘密鍵で署名されたものを、なりすましたいウォレットアドレスとして送っても拒否されること"""
    victim_wallet = Account.create().address
    attacker_account = Account.create()

    nonce = generate_siwe_nonce()
    forged_signature = _sign_login(attacker_account.key, victim_wallet, nonce)

    with pytest.raises(ValueError):
        authenticate_wallet(wallet_address=victim_wallet, signature=forged_signature, nonce=nonce)


def test_web3_wallet_login_rejects_replayed_nonce():
    """同じNonce・署名を2回使い回すリプレイ攻撃が拒否されること"""
    account = Account.create()
    wallet = account.address
    nonce = generate_siwe_nonce()
    signature = _sign_login(account.key, wallet, nonce)

    authenticate_wallet(wallet_address=wallet, signature=signature, nonce=nonce)

    with pytest.raises(ValueError):
        authenticate_wallet(wallet_address=wallet, signature=signature, nonce=nonce)


def test_magic_link_login():
    import uuid
    email = f"magic_{uuid.uuid4().hex[:6]}@example.com"

    token = request_magic_link(email)
    assert token

    user = verify_magic_link(token)
    assert user.email == email
    assert user.civic_id.startswith("市民#")

    # 同じユーザーで再度リクエストすると同一アカウントに紐づく
    token2 = request_magic_link(email)
    user2 = verify_magic_link(token2)
    assert user2.user_id == user.user_id


def test_magic_link_is_single_use():
    import uuid
    email = f"magic_{uuid.uuid4().hex[:6]}@example.com"
    token = request_magic_link(email)

    verify_magic_link(token)
    with pytest.raises(ValueError):
        verify_magic_link(token)


def test_magic_link_rejects_unknown_token():
    with pytest.raises(ValueError):
        verify_magic_link("not-a-real-token")


def test_magic_link_rejects_invalid_email():
    with pytest.raises(ValueError):
        request_magic_link("not-an-email")


def test_magic_link_expiry_is_enforced():
    import uuid
    import time as _time
    email = f"magic_{uuid.uuid4().hex[:6]}@example.com"
    token = request_magic_link(email)

    # 期限切れを模擬するため、Firestore上のexpires_atを過去に書き換える
    doc_ref = _magic_links_ref().document(_hash_magic_token(token))
    data = doc_ref.get().to_dict()
    doc_ref.set({**data, "expires_at": _time.time() - 1})

    with pytest.raises(ValueError):
        verify_magic_link(token)


def test_web3_wallet_login_rejects_unknown_nonce():
    """サーバーが発行していないNonceは拒否されること"""
    account = Account.create()
    wallet = account.address
    fake_nonce = "0" * 32
    signature = _sign_login(account.key, wallet, fake_nonce)

    with pytest.raises(ValueError):
        authenticate_wallet(wallet_address=wallet, signature=signature, nonce=fake_nonce)
