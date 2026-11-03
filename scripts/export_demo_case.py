"""デモケースを demo/cases/ にエクスポートするCLI（ピッチ準備用）。

使い方:
  python scripts/export_demo_case.py --list
  python scripts/export_demo_case.py --news-id <記録ID>
  python scripts/export_demo_case.py --news-id <記録ID> --with-audio
  python scripts/export_demo_case.py --news-id <記録ID> --out demo/cases

--list: 保存済みの記録（ピッチ候補）を一覧表示する
--with-audio: voice_archive に音声が無いとき、Gemini TTSで議論音声を生成してから保存する
  （cost_guard のTTS日次上限を消費するため、リハーサル前に一度だけ実行する想定。
   当日は書き出した Opus を再生し、TTS API を叩かない）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demo_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="デモケース（記録+音声）を demo/cases/ にエクスポートする")
    parser.add_argument("--list", action="store_true", help="ピッチ候補の記録一覧を表示する")
    parser.add_argument("--news-id", help="エクスポートする記録ID（--list で確認できる）")
    parser.add_argument("--out", default=str(demo_case.DEFAULT_OUT_DIR), help="出力先ディレクトリ（既定: demo/cases）")
    parser.add_argument("--with-audio", action="store_true", help="音声が無いときにGemini TTSで生成する（TTS日次上限を消費）")
    args = parser.parse_args()

    if args.list:
        rows = demo_case.list_cases()
        if not rows:
            print("記録がありません。先にニュースを分析してください。")
            return 0
        for r in rows:
            flags = "".join([
                "音声" if r["has_audio"] else "----",
                " 請求書" if r["has_request_detail"] else "",
                " サマリー" if r["has_summary"] else "",
            ])
            print(f"{r['news_id']}  怒り{r['overall_anger_level'] or '-'}  声{r['voices']}  提案{r['proposals']}  {flags}")
            print(f"    {r['title']}（{r['region']}）")
        return 0

    if not args.news_id:
        parser.error("--news-id か --list を指定してください")

    try:
        result = demo_case.export_case(args.news_id, out_dir=Path(args.out), synthesize=args.with_audio)
    except KeyError:
        print(f"記録が見つかりません: {args.news_id}（--list で一覧を確認してください）")
        return 1

    print(f"エクスポートしました: {result['json']}")
    if result["audio"]:
        print(f"音声: {result['audio']}")
    else:
        print("音声は未生成です。--with-audio で生成するか、アプリの「音声で聴く」を一度実行してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
