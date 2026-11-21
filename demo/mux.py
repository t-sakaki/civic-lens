"""録画（無音）に、アプリが生成した議論の音声を重ねる。 python demo/mux.py [--offset 秒]

capture.py --record で出た demo/out/video/*.webm と demo/out/audio/discussion.* を使い、
demo/out/demo.mp4 を作る。音声の開始位置は、既定では音声がブラウザに届いた時刻（≒再生開始）。
ずれたら --offset で秒数を指定して調整する。ffmpeg が必要。
"""
import argparse, json, subprocess, sys
from pathlib import Path

out = Path(__file__).parent / "out"
ap = argparse.ArgumentParser(); ap.add_argument("--offset", type=float)
a = ap.parse_args()
videos = sorted((out / "video").glob("*.webm"), key=lambda p: p.stat().st_mtime)
meta = out / "audio" / "discussion.json"
if not videos or not meta.exists():
    sys.exit("録画（demo/out/video）か音声（demo/out/audio）がありません。先に capture.py --record を実行してください")
m = json.loads(meta.read_text()); audio = out / "audio" / m["file"]
off = a.offset if a.offset is not None else m["arrived_at"]
ms = int(off * 1000)
cmd = ["ffmpeg", "-y", "-i", str(videos[-1]), "-i", str(audio),
       "-filter_complex", f"[1:a]adelay={ms}|{ms}[a]", "-map", "0:v", "-map", "[a]",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out / "demo.mp4")]
print("音声の開始位置:", off, "秒"); subprocess.run(cmd, check=True); print("作成:", out / "demo.mp4")
