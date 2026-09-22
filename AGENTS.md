# Civic Lens — Agent Guide

本プロジェクトは「第5回 Agentic AI Hackathon with Google Cloud」に参加するAIエージェントです。
開発・運用にあたり、以下のルールを遵守してください。

---

## 📌 ハッカソン基本情報

| 項目 | 内容 |
|---|---|
| 公式URL | https://zenn.dev/hackathons/google-cloud-japan-ai-hackathon-vol5 |
| 主催 | Zenn / Google Cloud Japan / クラスメソッド |
| 参加登録・提出締切 | **2026年10月15日（木）23:59** |
| 最終ピッチ | 2026年12月1日（火）渋谷ストリーム |
| Discord | https://discord.gg/cvA2Z3yny4 |

---

## ✅ 必須条件（遵守事項）

### 1. Google Cloud アプリケーション実行プロダクト（いずれか1つ以上）

本アプリケーションの実行に使用すること。

- Cloud Run ← **本プロジェクトはこれを使用**
- App Engine
- Google Kubernetes Engine（GKE）
- Compute Engine
- Cloud Run functions（旧 Cloud Functions）
- Cloud TPU / GPU

### 2. Google Cloud AI 技術（いずれか1つ以上）

本アプリケーションのAI機能に使用すること。

- Gemini API / Vertex AI ← **本プロジェクトはこれを使用**
- Gemma
- Agent Development Kit（ADK）
- Speech-to-Text / Text-to-Speech API
- Vision AI
- Natural Language AI
- Translation AI

### 3. 提出物

1. **GitHubリポジトリ連携**（公開・非公開問わず）
2. **デプロイURL**（Cloud Run上の動作するURL）
3. **プロジェクト説明**（日本語）:
   - 対象ユーザー像・課題・ソリューション
   - システムアーキテクチャ図
   - デモ動画（3分以内、YouTube公開）

### 4. 審査基準への対応

| 軸 | 対応内容 |
|---|---|
| 課題の新規性と解決策の有効性 | 情報公開請求の90%挫折問題を、AIエージェントで解決 |
| 自律性・エージェントらしさ | Gemini LlmAgent + SequentialAgent による自律的フロー |
| 実装品質と拡張性 | FastAPI + Cloud Run、17機関対応の拡張可能設計 |

---

## 🔑 API KEY 管理ルール（厳守）

### 基本原則
- 提供されたAPI KEYは**本ハッカソンの開発・デモにのみ使用**する
- ソースコードに**ハードコードしない**（`.env` 等で外部管理）
- GitHubリポジトリに**コミットしない**（`.gitignore` 厳守）
- ハッカソン終了後の利用は**各社の利用規約に従う**

### 現在のAPI KEY一覧

| 変数名 | 提供元 | 用途 | 状態 |
|---|---|---|---|
| `GEMINI_API_KEY` | Google Cloud | エージェント本体・条例マッチング | ✅ 設定済 |
| `GMI_API_KEY` | GMI Cloud | 条例・判例RAG推論 | ✅ 設定済 |
| `EKISPERT_API_KEY` | ヴァル研究所 | 駅すぱあとAPI（経路案内） | ✅ 設定済 |
| `YOUCAM_API_KEY` | Perfect Corp | YouCam API（表情解析） | ✅ 設定済 |
| `YOUCAM_PUBLIC_KEY` | Perfect Corp | YouCam RSA公開鍵 | ✅ 設定済 |

### 削除済み（不要）

| 変数名 | 理由 |
|---|---|
| `PARALLEL_API_KEY` | ハッカソン上の必須技術ではない（任意MCPツール） |

---

## 🚫 参加資格・禁止事項

### 参加資格
- 日本国内に居住する18歳以上の個人またはチーム
- 政府機関職員・スポンサー従業員は参加不可

### 禁止事項
- 提出後12月1日までのデフォルトブランチ修正
- 提出済みプロジェクトの記事・リポジトリ・動画の修正
- ハッカソン開始時点で一定進捗していたプロジェクトの提出

---

## 📁 GitHub管理ルール

- 提出時点の状態を12月1日まで保つ
- 審査はデフォルトブランチを対象
- 開発継続は別ブランチで行う
- 不要なファイル（`.env.bak`, `.cache/`, `.claude/`）はコミットしない

---

## 📝 参考: スポンサー技術（任意利用）

以下の技術は「任意」であり、利用の有無は審査に影響しない。

| スポンサー | 技術 | 本プロジェクトの利用 |
|---|---|---|
| ヴァル研究所 | 駅すぱあと API MCPサーバー | ✅ 利用（REST API直接呼び出し） |
| パーフェクト株式会社 | YouCam API | ✅ 利用 |
| GMI Cloud | AI推論プラットフォーム | ✅ 利用 |
| トレンドマイクロ | TrendAI Vision One | ❌ 未利用 |

---

## 🕵️ ニュース怒り再現パイプライン（新規エージェント構成）

**理念**: 日本には公的なオンブズマン制度が存在しない。市民は日々のニュースに怒りや違和感を覚えつつも、声を上げる気力を失い無関心を装いがちである。本パイプラインは、市民・国民の代わりにAIがニュースへ怒り、その怒りを情報公開請求という具体行動に変換することで、オンブズマン不在の機能的空白を埋めることを目的とする。ハッカソンの評価軸としても、既存の「情報公開請求支援」の枠を超え、行政監視の起点そのものをAIが担うという点でディスラプティブな提案と位置づけている。

一見すると好意的・中立に見えるニュース（例: 「アジア大会が成功裏に閉幕」）の裏にも、税金の使途・意思決定過程の不透明さ・住民負担といった、批判的に見れば疑わしい論点が存在しうる。それを見逃さず言語化し、実際に提出可能な開示請求の内容にまで落とし込むのが本パイプラインの役割である。

### エージェント構成（役割分割）

役割ごとに独立したモジュールとして実装している。

| エージェント | 実装 | 役割 |
|---|---|---|
| **NewsCollectorAgent** | `news_collector_agent.py` | 対象地域（市区町村名等）を入力に、Google News RSSからその地域の自治体・警察・公的行事に関するニュースを複数件自動収集する |
| **AngerReproductionAgent**（怒り再現エージェント） | `news_anger_agent.py` | ニュース本文から、批判的に見た場合の論点整理（`key_points`）と、対象地域の市民が怒っているかのような一人称の疑似的な声（`pseudo_citizen_voice`）を生成する。検索地域をプロンプトのヒントとして渡し、記事本文が具体的な自治体に触れていない場合でも誤った地域に紐づかないようにしている |
| **DisclosureRequestAgent**（開示請求エージェント、既存） | `agent.py`（`CivicLensAgent.analyze_anger`、`hint_authority_key`引数） | 疑似的な怒りの声を、開示請求書の該当箇所（対象機関・請求文書・根拠条例・請求理由要約・推奨対応期限）に変換する。返却された`target_authority_key`が条例DB（`ordinance_data.AUTHORITIES`）に存在しないスキーマ外の値だった場合は、ヒントまたはテキストマッチングで安全な値に自動補正する |
| **SocialPostingAgent**（SNSシェア） | `social_posting.py` | 再現された「疑似的な市民の声」を、ユーザー自身のX/Blueskyアカウントから投稿するためのテキスト生成・投稿処理。専用botアカウントは使わない |

### パイプラインの流れ

```
ユーザーの対象地域（登録市区町村 / GPS推定 / 行動履歴推定）
        ↓
NewsCollectorAgent.fetch_news(region)  ── Google News RSS から関連ニュースを複数件取得（一覧表示）
        ↓（ユーザーが記事を選択）
AngerReproductionAgent.generate(news_text, region) ── 論点整理 + 擬似市民の声
        ↓
DisclosureRequestAgent.analyze_anger(pseudo_citizen_voice, hint_authority_key) ── 開示請求書の該当箇所を生成
        ↓
news_reactions.py に記録として永続化（❤️😡😳リアクション、履歴一覧）
        ↓（ユーザーの任意操作）
SocialPostingAgent: X.com / Blueskyへシェア（未認証時は手動投稿用テキストを提示）
```

主なAPIエンドポイント（`app.py`）:
- `GET /api/news-agent/list` — 地域のニュース一覧取得
- `POST /api/news-agent/analyze` — 選択記事の怒り再現分析 + 記録保存
- `POST /api/news-agent/react` — リアクション付与
- `GET /api/news-agent/history` — 分析履歴一覧
- `POST /api/social/share` — SNSシェア（投稿 or フォールバックのシェアテキスト生成）

### 設計上の注意点

- ニュース取得はGoogle News RSS（APIキー不要）に依存する暫定実装
- 出力する開示請求書は氏名・住所等の個人情報欄を含まない。あくまで「対象機関・請求文書・根拠条例」等、客観的に特定可能な箇所のみを生成する
- AIによる「怒りの再現」はあくまで論点提示のためのフィクションであり、実在の個人の発言として提示・公表してはならない。SNSシェア時も投稿テキストに必ず免責文（`PSEUDO_VOICE_DISCLAIMER`）を含める
- 弁護士法72条遵守の方針（本ファイル冒頭・README参照）は本パイプラインにも適用される。AIは法的助言ではなく情報提供・書式作成支援に徹し、最終的に開示請求・SNS投稿を行うか否かの判断は必ずユーザー自身が行う
- GPSによる地域特定は `municipality_agent.py` / `municipality_pool.py` / `municipality_history.py`（既存のチャットUIコンポーザーに統合済み）を利用する。本パイプライン専用の簡易版地理エージェントは別途作らない

---

## ⚠️ 判断に迷ったら

- 公式ページ（LP）が一次情報: https://zenn.dev/hackathons/google-cloud-japan-ai-hackathon-vol5
- 運営への問い合わせ: zenn-support@classmethod.jp
- Discord: https://discord.gg/cvA2Z3yny4
