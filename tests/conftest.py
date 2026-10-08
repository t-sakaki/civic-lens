"""テスト共通の安全装置: 開発者の .env にある本物の保存先にテストが書き込まないようにする。

app.py は import 時に load_dotenv() で .env を読み込むが、すでに設定済みの環境変数
（空文字を含む）は上書きしない。そこで app の import より前（conftest の読み込み時）に、
Upstash の認証情報を空にし、保存先の既定をローカルJSONに固定する。

- Upstash を使うテストは、monkeypatch で URL/トークンを設定し通信をモックしている
- Firestore エミュレータのテストは、各テストが CIVIC_LENS_STORAGE=firestore を明示する
"""
import os

os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""
os.environ.setdefault("CIVIC_LENS_STORAGE", "json")


# Firebase（Auth/Firestore）を使うテストは、エミュレータがあるときだけ実行する。
# エミュレータが無い開発機では gcloud の認証情報（ADC）と .env の GOOGLE_CLOUD_PROJECT で
# 本物のプロジェクトに接続してしまい、テスト用ユーザーが本番に作られるため、接続口の
# firebase_client.get_app() を差し替えてスキップさせる。
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _never_touch_real_firebase(monkeypatch):
    if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        return
    import firebase_client

    def _skip():
        pytest.skip("Firebase Emulator が必要です（FIRESTORE_EMULATOR_HOST / FIREBASE_AUTH_EMULATOR_HOST）。本番には接続しません")

    monkeypatch.setattr(firebase_client, "get_app", _skip)


@pytest.fixture(autouse=True)
def _isolate_voice_archive_dir(monkeypatch, tmp_path_factory):
    """音声アーカイブのローカル保存先を、テストごとの一時フォルダに固定する。

    固定しないと、実際の /tmp/civic_lens_voice_archive に書き込み、前回の実行の記録が残って
    テストの結果が変わる（Firestoreを使うテストでは使われない）。
    """
    import voice_archive

    monkeypatch.setattr(voice_archive, "_DIR", tmp_path_factory.mktemp("voice_archive"))
