"""デモ用スクリーンショット（と任意で録画）を撮る。

前提: アプリ起動済み（例: python app.py → http://localhost:8080）、GEMINI_API_KEY 設定済み。
  python demo/capture.py                 # 全シーンのスクショ → demo/out/shots/
  python demo/capture.py --record        # 同時に動画 → demo/out/video/
  python demo/capture.py --only proposals
  python demo/capture.py --headed        # ブラウザを表示して確認しながら
画面が変わって失敗したショットは、警告を出して次へ進む（撮れた分だけ残す）。
"""
import argparse, json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text(encoding="utf-8"))
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8080")
ap.add_argument("--record", action="store_true")
ap.add_argument("--headed", action="store_true")
ap.add_argument("--only", default="")
args = ap.parse_args()

out = here / "out"
shots_dir = out / "shots"; shots_dir.mkdir(parents=True, exist_ok=True)
wanted = set(args.only.split(",")) if args.only else None
enabled = {sh for s in plan["scenes"] if s.get("enabled", True) for sh in s["shots"]}
enabled |= set(plan.get("extra_shots", []))
if wanted: enabled &= wanted
SLOW = 120_000  # AI応答待ち


def snap(page, name):
    if name in enabled:
        p = shots_dir / f"{name}.png"
        page.screenshot(path=str(p)); print("撮影:", p)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not args.headed)
    ctx_kw = {"viewport": plan["viewport"], "device_scale_factor": 2, "locale": "ja-JP"}
    if args.record:
        ctx_kw["record_video_dir"] = str(out / "video")
        ctx_kw["record_video_size"] = plan["viewport"]
    ctx = browser.new_context(**ctx_kw)
    page = ctx.new_page()
    try:
        page.goto(args.url); page.wait_for_load_state("networkidle")
        # 対象地域を手動指定（位置情報・履歴の実データを映さない）
        page.click("#plusMenuBtn")
        page.select_option("#manualPrefectureSelect", label=plan["prefecture"])
        page.fill("#manualMunicipalityInput", plan["region"])
        page.click("#manualMunicipalityBtn")
        page.wait_for_selector(".start-news-item", timeout=SLOW)
        page.locator(".start-news-item", has_text=plan["news_title_contains"]).first.wait_for(timeout=SLOW)
        snap(page, "news-list")

        entry = page.locator(".start-news-entry", has_text=plan["news_title_contains"]).first
        entry.locator(".start-news-agent").first.wait_for(timeout=SLOW)
        snap(page, "agents-appear")

        entry.locator(".start-news-agent-summary").click()
        page.wait_for_selector(".news-proposal-btn", timeout=SLOW)
        page.wait_for_timeout(1500)
        snap(page, "proposals")
        snap(page, "disclaimer")

        page.locator(".news-proposal-btn").first.click()
        page.wait_for_timeout(SLOW // 6)
        snap(page, "request-detail")
    except PWTimeout as e:
        print("⚠️ 画面待ちがタイムアウト。UIが変わった可能性:", e, file=sys.stderr)
    except Exception as e:
        print("⚠️ 失敗:", e, file=sys.stderr)
    finally:
        if "architecture" in enabled:
            arch = here.parent / "docs" / "architecture.png"
            if arch.exists():
                (shots_dir / "architecture.png").write_bytes(arch.read_bytes()); print("コピー:", arch)
        ctx.close(); browser.close()
