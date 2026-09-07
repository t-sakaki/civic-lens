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

## ⚠️ 判断に迷ったら

- 公式ページ（LP）が一次情報: https://zenn.dev/hackathons/google-cloud-japan-ai-hackathon-vol5
- 運営への問い合わせ: zenn-support@classmethod.jp
- Discord: https://discord.gg/cvA2Z3yny4
