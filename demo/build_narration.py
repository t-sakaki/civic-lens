"""plan.json からナレーション原稿 narration.md を生成する。 python demo/build_narration.py"""
import json
from pathlib import Path

here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text(encoding="utf-8"))
scenes = [s for s in plan["scenes"] if s.get("enabled", True)]
total = sum(s["seconds"] for s in scenes)
lines = [f"# デモ ナレーション原稿（合計 約{total}秒）", "",
         "> plan.json から自動生成。直接編集せず plan.json を直してください。", ""]
if total > 180:
    lines += [f"⚠️ 合計が3分（180秒）を超えています: {total}秒", ""]
for i, s in enumerate(scenes, 1):
    lines += [f"## {i}. {s['title']}（{s['seconds']}秒）", "", s["narration"], ""]
(here / "narration.md").write_text("\n".join(lines), encoding="utf-8")
print(f"narration.md を生成: {len(scenes)}シーン / {total}秒")
