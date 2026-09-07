"""Civic Lens — Firebase Admin SDK 初期化

Cloud Run上ではアタッチされたサービスアカウントの
Application Default Credentials (ADC) を自動的に利用するため、
サービスアカウントキーファイルの配布は不要。

Vercel等のサーバーレス環境ではADCが使えないため、サービスアカウントキーの
JSON全文を FIREBASE_SERVICE_ACCOUNT_JSON 環境変数に設定することで対応する。

ローカル開発では `gcloud auth application-default login` を実行するか、
GOOGLE_APPLICATION_CREDENTIALS 環境変数でキーファイルを指定する。

テスト/CIでは Firestore/Auth Emulator（FIRESTORE_EMULATOR_HOST /
FIREBASE_AUTH_EMULATOR_HOST）を使うことで、実際のGCP認証情報なしに
Firebase依存のコードを検証できる。エミュレータはトークンを検証しないため、
ダミーの匿名クレデンシャルで初期化する。

初期化失敗はモジュールのimport時ではなく、実際にFirestoreへアクセスする
タイミングで例外を送出する。認証・GitHub化機能等がFirebase設定に依存するため、
未設定でもアプリ全体の起動を妨げないようにするため。
"""
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from google.auth.credentials import AnonymousCredentials

_app = None


class _EmulatorCredentials(credentials.Base):
    """Firestore/Auth Emulator向けのダミー認証情報（トークン検証されないため中身は問わない）"""

    def get_credential(self):
        return AnonymousCredentials()


def _using_emulator() -> bool:
    return bool(os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"))


def get_app() -> firebase_admin.App:
    """Firebase Admin アプリをプロセス内で一度だけ初期化して返す"""
    global _app
    if _app is not None:
        return _app

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    options = {"projectId": project_id} if project_id else None

    try:
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            cred = credentials.Certificate(json.loads(service_account_json))
            _app = firebase_admin.initialize_app(cred, options)
        elif _using_emulator():
            _app = firebase_admin.initialize_app(_EmulatorCredentials(), options)
        else:
            # Cloud Run: アタッチされたサービスアカウントのADCを自動利用
            # ローカル: `gcloud auth application-default login` のADCを利用
            _app = firebase_admin.initialize_app(options=options)
        return _app
    except Exception as e:
        raise RuntimeError(
            "Firebase Admin SDKの初期化に失敗しました。サーバーレス環境（Vercel等）では "
            "FIREBASE_SERVICE_ACCOUNT_JSON 環境変数にサービスアカウントキーのJSON全文を設定してください。"
            "Cloud Run/ローカル環境では Application Default Credentials が利用可能か確認してください。"
        ) from e


def get_firestore_client() -> firestore.Client:
    get_app()
    return firestore.client()
