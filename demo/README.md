# デモ素材（提出用動画・プレゼン用スクショ）

計画は `plan.json` に集約。後から自由に変更できる。

| やること | コマンド |
|---|---|
| 計画を変更 | `plan.json` を編集（シーンの順序・秒数・ナレーション・`enabled`） |
| ナレーション原稿を作り直す | `python demo/build_narration.py`（3分超過を警告） |
| スクショを撮る | `python demo/capture.py`（`--only proposals` で1枚だけ、`--headed` で目視） |
| 画面録画も撮る | `python demo/capture.py --record` → `demo/out/video/` |

アプリを先に起動しておくこと（`python app.py`）。出力は `demo/out/`（git管理外）。

## デモケース（ピッチ当日の再現用）

ピッチで再現するニュース1件分の記録（SDGsエージェントの声・開示請求の提案・請求書分析）と、
対応する音声（あれば）を `demo/cases/` に保存する。当日はこのケースを再生し、
ネットワーク障害やTTS日次上限に左右されない。

| やること | コマンド |
|---|---|
| ピッチ候補の一覧を見る | `python scripts/export_demo_case.py --list` |
| ケースを保存する | `python scripts/export_demo_case.py --news-id <記録ID>` |
| 音声も生成して保存する | `python scripts/export_demo_case.py --news-id <記録ID> --with-audio`（TTS日次上限を消費。リハーサル前に一度だけ） |

- 音声は voice_archive に保存済みの議論音声（Gemini TTS・冒頭に免責読み上げつき）を使う
- 当日の再生は `demo/cases/*.opus` のローカル再生を基本とし、TTS API を叩かない
- 記録には擬似市民の声の免責文（`PSEUDO_VOICE_DISCLAIMER`）と記事の出典URLが必ず入る

## 録画前チェック（AGENTS.md の原則）
- 「市民の声」がAI生成のフィクションだと分かる免責文が映っているか
- 記事の内容が実在の報道と一致しているか（事実と違う画面を映さない）
- 位置情報・履歴に実住所などが映っていないか（スクリプトは地域を手動指定する）
- 提出後は動画・リポジトリを修正できない。最終版の画面で撮ること
