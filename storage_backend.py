"""Civic Lens — 永続化先の切り替え（Firestore / ローカルJSON）

サーバーレス環境（Vercel等）のローカルファイルは再起動で消えるため、本番では Firestore を使う。
Firestore の認証情報がない開発環境に限り、ローカルJSONを予備として使う。

判定順:
  1. CIVIC_LENS_STORAGE=firestore|json が明示されていればそれに従う
  2. Firestore の認証情報・エミュレータ・Cloud Run 環境のいずれかがあれば Firestore
  3. それ以外はローカルJSON（Vercel上でこれに該当する場合は、データが消えることを警告する）
"""
import os
import sys

_warned = False


def use_firestore() -> bool:
    global _warned
    mode = os.getenv("CIVIC_LENS_STORAGE", "").strip().lower()
    if mode in ("firestore", "json"):
        return mode == "firestore"
    if any(
        os.getenv(k)
        for k in ("FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS", "FIRESTORE_EMULATOR_HOST", "K_SERVICE")
    ):
        return True
    if os.getenv("VERCEL") and not _warned:
        _warned = True
        print(
            "[storage] Firestore の認証情報がないため /tmp のJSONに保存します。再起動でデータが消えます。"
            "FIREBASE_SERVICE_ACCOUNT_JSON を設定してください。",
            file=sys.stderr,
        )
    return False
