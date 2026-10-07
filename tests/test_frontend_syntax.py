"""templates/index.html のインラインJSの構文チェック。

構文エラーが1つあると、そのスクリプト全体のイベント登録が止まり、画面のボタンが全部効かなくなる
（Pythonのテストでは検出できない）。node が無い環境ではスキップする。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要です")
@pytest.mark.parametrize("name", ["index.html", "ledger.html"])
def test_inline_scripts_have_valid_syntax(name, tmp_path):
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts
    for i, body in enumerate(scripts):
        f = tmp_path / f"{name}.{i}.js"
        f.write_text(body, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name} のインラインスクリプト#{i}に構文エラー:\n{r.stderr[:600]}"
