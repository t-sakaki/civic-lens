"""デモ用スクリーンショット（と任意で録画）を撮る。

前提: アプリ起動済み（例: python app.py → http://localhost:8080）、GEMINI_API_KEY 設定済み。
  python demo/capture.py                 # 全シーンのスクショ → demo/out/shots/
  python demo/capture.py --record        # 同時に動画 → demo/out/video/
  python demo/capture.py --only proposals
  python demo/capture.py --headed        # ブラウザを表示して確認しながら
画面が変わって失敗したショットは、警告を出して次へ進む（撮れた分だけ残す）。
"""
import argparse, json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text(encoding="utf-8"))
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8080")
ap.add_argument("--record", action="store_true")
ap.add_argument("--headed", action="store_true")
ap.add_argument("--only", default="")
ap.add_argument("--show-ledger", action="store_true", help="「みんなの請求」の帯・欄（開発中は試験データが混ざる）を隠さない")
args = ap.parse_args()

out = here / "out"
shots_dir = out / "shots"; shots_dir.mkdir(parents=True, exist_ok=True)
wanted = set(args.only.split(",")) if args.only else None
enabled = {sh for s in plan["scenes"] if s.get("enabled", True) for sh in s["shots"]}
enabled |= set(plan.get("extra_shots", []))
if wanted: enabled &= wanted
SLOW = 120_000  # AI応答待ち
ANALYZE = 360_000  # 声の統合は複数回のAI呼び出しで数分かかることがある


def step(msg):
    print("▶", msg, flush=True)


def snap(page, name):
    if name in enabled:
        p = shots_dir / f"{name}.png"
        page.screenshot(path=str(p)); print("撮影:", p)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not args.headed, args=["--mute-audio"])
    ctx_kw = {"viewport": plan["viewport"], "device_scale_factor": 2, "locale": "ja-JP"}
    if args.record:
        ctx_kw["record_video_dir"] = str(out / "video")
        ctx_kw["record_video_size"] = plan["viewport"]
    ctx = browser.new_context(**ctx_kw)
    page = ctx.new_page()
    t0 = time.time()  # 録画の開始（コンテキスト作成）とほぼ同時
    audio_dir = out / "audio"; audio_dir.mkdir(parents=True, exist_ok=True)

    def on_response(resp):
        # アプリが生成した「エージェントたちの議論」の音声を、ブラウザの再生とは別に保存する
        # （Playwrightの録画には音声が入らないため。動画への合成は demo/mux.py）
        if "/api/news-agent/voice-panel" in resp.url and resp.ok:
            try:
                ext = "opus" if "ogg" in (resp.headers.get("content-type") or "") else "wav"
                f = audio_dir / f"discussion.{ext}"
                f.write_bytes(resp.body())
                (audio_dir / "discussion.json").write_text(json.dumps({"file": f.name, "arrived_at": round(time.time() - t0, 2)}))
                print("音声を保存:", f)
            except Exception as e:
                print("⚠️ 音声の保存に失敗:", e, file=sys.stderr)
    page.on("response", on_response)
    try:
        page.goto(args.url, wait_until="domcontentloaded"); page.wait_for_selector("#authorityTag", timeout=SLOW); page.wait_for_timeout(3000)
        if not args.show_ledger:
            # 開発環境の試験データ（test attestation 等）が映らないよう、台帳の帯と欄だけを隠す
            page.add_style_tag(content=".minna-ticker-bar{display:none!important}")
            page.evaluate("""() => { const h = [...document.querySelectorAll('h2')].find(e => e.textContent.includes('みんなの請求'));
                if (h) { const sec = h.closest('section') || h.parentElement.parentElement; sec.style.display = 'none'; } }""")
        # 対象地域を手動指定（位置情報・履歴の実データを映さない）
        step("対象機関タグを開く")
        # ホバーで開く（クリックすると、ホバーで開いた直後にトグルで閉じてしまう）
        page.hover("#authorityTag")
        page.wait_for_selector("#authorityPopover:not(.hidden)", timeout=5000)
        step("都道府県・市区町村を設定")
        page.select_option("#manualPrefectureSelect", label=plan["prefecture"])
        page.fill("#manualMunicipalityInput", plan["region"])
        page.click("#manualMunicipalityBtn")
        page.wait_for_timeout(2000)
        step("＋メニューを開いてニュースを待つ")
        # ニュースは「＋」メニュー内に出る
        if page.locator("#plusMenuPopover.hidden").count():
            page.click("#plusMenuBtn")
        page.wait_for_selector(".start-news-item", timeout=SLOW)
        page.locator(".start-news-item", has_text=plan["news_title_contains"]).first.wait_for(timeout=SLOW)
        snap(page, "news-list")

        step("エージェントの登場を待つ")
        entry = page.locator(".start-news-entry", has_text=plan["news_title_contains"]).first
        entry.locator(".start-news-agent").first.wait_for(timeout=SLOW)
        snap(page, "agents-appear")

        step("声をまとめて提案を待つ")
        entry.locator(".start-news-agent-summary").click()
        page.locator(".news-proposal-btn:visible").first.wait_for(state="visible", timeout=ANALYZE)
        page.locator(".news-proposal-btn:visible").first.evaluate("el => el.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(1500)
        snap(page, "proposals")
        snap(page, "disclaimer")

        step("提案を選んで請求の詳細を待つ")
        page.locator(".news-proposal-btn:visible").first.click()
        # 詳細分析の結果は右ペイン（#resultSection）に出る
        page.wait_for_selector("#resultSection:not(.hidden)", timeout=ANALYZE)
        page.wait_for_timeout(2500)
        snap(page, "request-detail")
    except Exception as e:
        print("⚠️ 失敗（直前の ▶ の手順で止まりました）:", str(e).splitlines()[0], file=sys.stderr)
        dbg = out / "debug-failure.png"
        page.screenshot(path=str(dbg)); print("止まった時点の画面:", dbg, file=sys.stderr)
    finally:
        if "architecture" in enabled:
            arch = here.parent / "docs" / "architecture.png"
            if arch.exists():
                (shots_dir / "architecture.png").write_bytes(arch.read_bytes()); print("コピー:", arch)
        ctx.close(); browser.close()
