"""公開ダッシュボード（Workers）のニュース2件を録画し、音声を重ねて mp4 にする。
  .venv/bin/python demo/record_dashboard.py   → demo/out/dashboard/example1.mp4, example2.mp4
Playwrightの録画は無音のため、Audio.play の開始時刻を記録して ffmpeg で音声を合成する。
"""
import json, subprocess, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://sdgs-anger-dashboard.taira-sakakibara.workers.dev/"
OUT = Path(__file__).parent / "out" / "dashboard"; OUT.mkdir(parents=True, exist_ok=True)
VP = {"width": 1600, "height": 900}
HOOK = """(()=>{window.__plays=[];const o=HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play=function(){window.__plays.push([performance.now(),this.currentSrc||this.src]);return o.apply(this,arguments)}})()"""
EXAMPLES = [("example1", 0, "徳島市"), ("example2", 1, "久喜市")]

def mux(webm, plays, t_start, dst):
    cmd = ["ffmpeg", "-y", "-i", str(webm)]; filt = []; labs = []
    for i, (t, src) in enumerate(plays):
        f = OUT / Path(src).name
        if not f.exists(): f.write_bytes(urllib.request.urlopen(urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0'})).read())
        cmd += ["-i", str(f)]; ms = int(max(0, t - t_start))
        filt.append(f"[{i+1}:a]adelay={ms}|{ms}[a{i}]"); labs.append(f"[a{i}]")
    if not plays:
        subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)], check=True); return
    filt.append("".join(labs) + f"amix=inputs={len(labs)}:normalize=0[a]")
    subprocess.run(cmd + ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[a]", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", str(dst)], check=True)

with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    for name, idx, label in EXAMPLES:
        vdir = OUT / f"v_{name}"
        ctx = br.new_context(viewport=VP, locale="ja-JP", record_video_dir=str(vdir), record_video_size=VP)
        ctx.add_init_script(HOOK); T0 = time.time() * 1000; page = ctx.new_page()
        page.goto(URL); page.wait_for_selector("#hist li"); page.wait_for_timeout(2500)
        page.locator("#hist li").nth(idx).click()          # 記事を選ぶ（音声があれば自動で再生）
        page.wait_for_timeout(1200)
        if page.evaluate("window.__plays.length"):
            page.wait_for_function("document.getElementById('speakBtn').textContent.includes('読み上げ')", timeout=120_000)
            page.wait_for_timeout(2000)
        else:                                               # 音声が無い記事: 怒りの強い順にタイルを見せる
            rows = page.locator(".vrow")
            for i in range(rows.count()):
                rows.nth(i).hover(); page.wait_for_timeout(3000)
            page.wait_for_timeout(1500)
        org = page.evaluate("performance.timeOrigin"); plays = [[org + t - T0, u] for t, u in page.evaluate("window.__plays")]
        webm = Path(page.video.path()); ctx.close()
        # 録画開始≒ページ生成時。再生時刻は performance.now 基準なので、ページ読み込み開始との差で補正
        mux(webm, plays, 0, OUT / f"{name}.mp4"); print("作成:", OUT / f"{name}.mp4", f"({label}, 音声{len(plays)}本)")
    br.close()
