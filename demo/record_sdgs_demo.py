"""提出用デモ録画（市民の訴え → SDGs怒りエージェント団 → 開示請求書 → 結果記録）

使い方: python demo/record_sdgs_demo.py --url https://civic-lens-259303826380.asia-northeast1.run.app
出力: demo/out/video-sdgs/*.webm（無音。ナレーションは demo/narration_sdgs.md）
結果ボタンは本番データを汚さないよう「未提出」を押す。
"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

GRIEVANCE = ("豊田市が駅前の公園の樹木を大量に伐採して再開発を進めている。業者は随意契約で決まったらしく、"
             "住民説明会も開かれていない。税金がいくら使われているのかも分からない。")

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8080")
ap.add_argument("--headed", action="store_true")
args = ap.parse_args()
out = Path(__file__).parent / "out" / "video-sdgs"
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(headless=not args.headed)
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(out),
                        record_video_size={"width": 1440, "height": 900}, locale="ja-JP")
    pg = ctx.new_page()
    pg.goto(args.url, wait_until="networkidle")
    pg.wait_for_timeout(4000)
    box = pg.locator("#userInput")
    box.click()
    box.type(GRIEVANCE, delay=35)
    pg.wait_for_timeout(1000)
    pg.locator("#analyzeBtn").click()
    pg.wait_for_selector(".sdgs-anger-panel", timeout=150000)
    pg.wait_for_timeout(1500)
    panel = pg.locator(".sdgs-anger-panel")
    panel.scroll_into_view_if_needed()
    pg.wait_for_timeout(9000)
    pg.mouse.wheel(0, 500); pg.wait_for_timeout(6000)
    pg.locator("#documentsList").scroll_into_view_if_needed(); pg.wait_for_timeout(6000)
    pg.locator(".chat-suggestion-btn", has_text="プランA").last.click()
    pg.wait_for_selector(".outcome-panel", timeout=180000)
    pg.locator("#outputSection").scroll_into_view_if_needed(); pg.wait_for_timeout(8000)
    pg.locator(".outcome-panel").scroll_into_view_if_needed(); pg.wait_for_timeout(3000)
    pg.locator('.outcome-panel button[data-outcome="not_submitted"]').click()
    pg.wait_for_timeout(6000)
    pg.screenshot(path=str(out / "final.png"))
    ctx.close(); b.close()
print("saved to", out)