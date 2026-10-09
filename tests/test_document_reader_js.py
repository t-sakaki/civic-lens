"""画面（templates/index.html の docSpeech）と document_reader の文の分割が一致することの確認。

自然な声の再生区間（X-Doc-Timing）は、サーバーの分割ごとの時刻。画面が同じ区切りで分けないと、
ハイライトが音声とずれる。Node が無い環境ではスキップする。
"""
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import document_reader as dr

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js が必要")
INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _js_functions() -> str:
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("function splitText(text, limit) {")
    end = html.index("function highlight(el, text, span)")
    return html[start:end]


def _samples():
    random.seed(7)
    words = ["質問", "県", "対応", "、", "。", "！", "？", " ", "\n", "個人情報", "漏洩", "再発防止", "\n\n"]
    fixed = [
        "一般質問通告書（愛知県議会）\n\n1. あいこんナビの個人情報誤掲載について（答弁者: 福祉局長）\n  (1) 発覚の経緯と県の対応を伺う。",
        "議長のお許しをいただきましたので、通告に従い質問いたします。" * 10,
        "あ、" * 300,
        "句点のない長い文が続きますが、読点で分けられるはずです、" * 12 + "最後。",
        "A sentence without japanese. Another one! And?  Third",
    ]
    return fixed + ["".join(random.choice(words) for _ in range(random.randint(5, 400))) for _ in range(40)]


def test_js_split_matches_python_split(tmp_path):
    cases = []
    for text in _samples():
        body = re.sub(r"[ \t　]+", " ", text.strip())
        for limit in (dr.CHUNK_CHARS, 60):
            cases.append({"text": body, "limit": limit, "py": dr.split_text(body, limit)})
    (tmp_path / "cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    script = _js_functions() + """
const cases = require('./cases.json');
const bad = cases.filter(c => JSON.stringify(splitText(normalize(c.text), c.limit)) !== JSON.stringify(c.py));
console.log(JSON.stringify({ total: cases.length, bad: bad.length }));
"""
    (tmp_path / "run.js").write_text(script, encoding="utf-8")
    out = subprocess.run(["node", "run.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"total": len(cases), "bad": 0}


def test_js_locate_finds_every_chunk_in_the_original_text(tmp_path):
    script = _js_functions() + r"""
const notice = "一般質問通告書（愛知県議会）\n\n1. あいこんナビの個人情報誤掲載について（答弁者: 福祉局長）\n  (1) 発覚の経緯と県の対応を伺う。\n  (2)  再発防止策を\t伺う。\n\n$特殊 (記号) [括弧] *星* +加+ ?疑? 文字。";
const result = [30, 60, 260].map(limit => {
  const spans = locate(notice, splitText(normalize(notice), limit));
  let prev = 0, ordered = true;
  spans.forEach(s => { if (s) { if (s[0] < prev) ordered = false; prev = s[1]; } });
  return { limit, missing: spans.filter(s => !s).length, ordered };
});
console.log(JSON.stringify(result));
"""
    (tmp_path / "run.js").write_text(script, encoding="utf-8")
    out = subprocess.run(["node", "run.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert all(r["missing"] == 0 and r["ordered"] for r in json.loads(out.stdout))
