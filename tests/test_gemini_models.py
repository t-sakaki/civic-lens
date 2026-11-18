"""gemini_models: モデルの優先順位・安価/無料モデルへの降格・フォールバックとスキップ制御"""
from google.genai import types
import pytest

import cost_guard
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
        self.configs = []
        self.models = self
        self._behavior = behavior

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        self.configs.append(config)
        result = self._behavior[model]
        if callable(result):
            result = result(config)
        if isinstance(result, Exception):
            raise result
        return _Resp(result)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    gm.reset_state()
    cost_guard.reset_state()
    for var in (
        "GEMINI_MODELS_PRO",
        "GEMINI_MODELS_BALANCED",
        "GEMINI_MODELS_FLASH",
        "GEMINI_MODELS_FREE",
        "GEMINI_LOW_COST_MODE",
        "GEMINI_SOFT_DAILY_LIMIT",
        "GEMINI_DAILY_CALL_LIMIT",
        "GEMINI_THINKING_PRO",
        "GEMINI_THINKING_BALANCED",
        "GEMINI_THINKING_FLASH",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    gm.reset_state()
    cost_guard.reset_state()


def test_default_chains_and_env_override(monkeypatch):
    pro = gm.model_chain("pro")
    assert pro[0] == "gemini-4-argon"
    assert pro[1] == "gemini-4-hydrogen"
    assert pro[-1] == "gemini-flash-lite-latest"
    assert gm.model_chain("balanced")[0] == "gemini-4-hydrogen"
    assert gm.model_chain("flash")[0] == "gemini-4-hydrogen"
    assert "gemini-4-argon" not in gm.model_chain("free")

    monkeypatch.setenv("GEMINI_MODELS_PRO", "model-a, model-b")
    assert gm.model_chain("pro") == ["model-a", "model-b"]


def test_falls_back_to_cheap_model_on_404_or_403():
    client = _FakeClient({
        "gemini-4-argon": _Err(404),
        "gemini-4-hydrogen": "ok-hydrogen",
        "gemini-3.8-flash": "ok-flash",
        "gemini-3.5-flash-lite": "ok-lite",
        "gemini-flash-lite-latest": "ok-latest",
    })
    assert gm.generate(client, "pro", "hi").text == "ok-hydrogen"
    assert client.calls == ["gemini-4-argon", "gemini-4-hydrogen"]


def test_dead_or_zero_quota_model_is_skipped_on_next_call():
    client = _FakeClient({
        "gemini-4-argon": _Err(429, "RESOURCE_EXHAUSTED: free_tier limit: 0"),
        "gemini-4-hydrogen": "ok-hydrogen",
        "gemini-3.8-flash": "ok-flash",
        "gemini-3.5-flash-lite": "ok-lite",
        "gemini-flash-lite-latest": "ok-latest",
    })
    gm.generate(client, "pro", "1")
    client.calls.clear()
    gm.generate(client, "pro", "2")
    assert client.calls == ["gemini-4-hydrogen"]  # 有料専用でクォータ0のArgonは再試行しない


def test_rate_limit_skips_temporarily_and_transient_errors_do_not():
    client = _FakeClient({
        "gemini-4-argon": _Err(500),
        "gemini-4-hydrogen": _Err(429, "rate limit"),
        "gemini-3.8-flash": "ok-flash",
        "gemini-3.5-flash-lite": "ok-lite",
        "gemini-flash-lite-latest": "ok-latest",
    })
    assert gm.generate(client, "pro", "x").text == "ok-flash"
    names = [m for m, _ in gm.attempts("pro")]
    assert "gemini-4-argon" in names  # 500は飛ばさない
    assert "gemini-4-hydrogen" not in names  # 429は一時スキップ


def test_low_cost_mode_skips_paid_only_models(monkeypatch):
    monkeypatch.setenv("GEMINI_LOW_COST_MODE", "1")
    names = [m for m, _ in gm.attempts("pro")]
    assert "gemini-4-argon" not in names
    assert names[0] == "gemini-4-hydrogen"
    assert gm.primary_model("pro") == "gemini-4-hydrogen"


def test_soft_daily_limit_downgrades_to_cheap_models(monkeypatch):
    monkeypatch.setenv("GEMINI_SOFT_DAILY_LIMIT", "2")
    client = _FakeClient({
        "gemini-4-argon": "ok-argon",
        "gemini-4-hydrogen": "ok-hydrogen",
        "gemini-3.8-flash": "ok-flash",
        "gemini-3.5-flash-lite": "ok-lite",
        "gemini-flash-lite-latest": "ok-latest",
    })
    assert gm.generate(client, "pro", "1").text == "ok-argon"
    assert gm.generate(client, "pro", "2").text == "ok-argon"
    # 3回目以降はソフト上限到達により有料専用のArgonを外して安価/無料枠モデルへ降格
    assert gm.generate(client, "pro", "3").text == "ok-hydrogen"
    assert client.calls == ["gemini-4-argon", "gemini-4-argon", "gemini-4-hydrogen"]


def test_timeouts_preserve_pro_budget_for_argon():
    pro_attempts = dict(gm.attempts("pro"))
    assert pro_attempts["gemini-4-argon"] == gm.PRO_TIMEOUT_S
    assert pro_attempts["gemini-4-hydrogen"] == gm.BALANCED_TIMEOUT_S
    assert pro_attempts["gemini-3.8-flash"] == gm.LIGHT_TIMEOUT_S
    assert pro_attempts["gemini-3.5-flash-lite"] == gm.LIGHT_TIMEOUT_S

    flash_attempts = dict(gm.attempts("flash"))
    assert flash_attempts["gemini-4-hydrogen"] == gm.LIGHT_TIMEOUT_S


def test_thinking_level_applied_per_tier_and_not_to_lite():
    base = types.GenerateContentConfig(response_mime_type="application/json")
    argon_cfg = gm.config_for_tier("pro", "gemini-4-argon", base)
    assert argon_cfg.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert base.thinking_config is None  # 元のconfigは変更しない

    flash_cfg = gm.config_for_tier("flash", "gemini-4-hydrogen", base)
    assert flash_cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW

    lite_cfg = gm.config_for_tier("pro", "gemini-3.5-flash-lite", base)
    assert lite_cfg is base


def test_retries_same_model_without_thinking_config_on_400():
    def handler(cfg):
        if cfg is not None and getattr(cfg, "thinking_config", None) is not None:
            raise _Err(400, "INVALID_ARGUMENT: thinking_level is not supported")
        return "ok-no-thinking"

    client = _FakeClient({"gemini-4-argon": handler})
    assert gm.generate(client, "pro", "x").text == "ok-no-thinking"
    assert client.calls == ["gemini-4-argon", "gemini-4-argon"]


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
    gm.report_failure("gemini-4-argon", _Err(404))
    assert gm.primary_model("pro") == "gemini-4-hydrogen"
