"""gemini_models: モデルのフォールバックチェーンとスキップ制御"""
import pytest

import gemini_models as gm


class _Err(Exception):
    def __init__(self, code, msg="err"):
        super().__init__(f"{code} {msg}")
        self.code = code


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    """model名ごとに成功/失敗を切り替えるダミークライアント"""

    def __init__(self, behavior):
        self.calls = []
        self.models = self
        self._behavior = behavior

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        result = self._behavior[model]
        if isinstance(result, Exception):
            raise result
        return _Resp(result)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    gm.reset_state()
    monkeypatch.delenv("GEMINI_MODELS_PRO", raising=False)
    monkeypatch.delenv("GEMINI_MODELS_FLASH", raising=False)
    yield
    gm.reset_state()


def test_default_chains_and_env_override(monkeypatch):
    assert gm.model_chain("pro")[0] == "gemini-pro-latest"
    assert gm.model_chain("flash")[0] == "gemini-flash-latest"
    monkeypatch.setenv("GEMINI_MODELS_PRO", "model-a, model-b")
    assert gm.model_chain("pro") == ["model-a", "model-b"]


def test_falls_back_to_next_model_on_404():
    client = _FakeClient({
        "gemini-pro-latest": _Err(404),
        "gemini-flash-latest": "ok",
        "gemini-flash-lite-latest": "lite",
    })
    assert gm.generate(client, "pro", "hi").text == "ok"
    assert client.calls == ["gemini-pro-latest", "gemini-flash-latest"]


def test_dead_model_is_skipped_on_next_call():
    client = _FakeClient({
        "gemini-pro-latest": _Err(404),
        "gemini-flash-latest": "ok",
        "gemini-flash-lite-latest": "lite",
    })
    gm.generate(client, "pro", "1")
    client.calls.clear()
    gm.generate(client, "pro", "2")
    assert client.calls == ["gemini-flash-latest"]  # 404のproは再試行しない


def test_rate_limit_skips_temporarily_and_transient_errors_do_not():
    client = _FakeClient({
        "gemini-pro-latest": _Err(500),
        "gemini-flash-latest": _Err(429),
        "gemini-flash-lite-latest": "lite",
    })
    assert gm.generate(client, "pro", "x").text == "lite"
    names = [m for m, _ in gm.attempts("pro")]
    assert "gemini-pro-latest" in names  # 500は飛ばさない
    assert "gemini-flash-latest" not in names  # 429は一時スキップ


def test_raises_last_error_when_all_fail():
    client = _FakeClient({m: _Err(403) for m in gm.model_chain("flash")})
    with pytest.raises(_Err):
        gm.generate(client, "flash", "x")


def test_all_models_skipped_still_tries_everything():
    client = _FakeClient({m: _Err(404) for m in gm.model_chain("flash")})
    with pytest.raises(_Err):
        gm.generate(client, "flash", "x")
    # 全滅状態でも回復を確認できるよう、全モデルを再試行する
    assert [m for m, _ in gm.attempts("flash")] == gm.model_chain("flash")


def test_primary_model_skips_dead():
    gm.report_failure("gemini-pro-latest", _Err(404))
    assert gm.primary_model("pro") == "gemini-flash-latest"
