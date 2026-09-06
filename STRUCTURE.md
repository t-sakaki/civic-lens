# Civic Lens — プロジェクト構造

## ディレクトリ
```
civic-lens/
├── backend/                    # FastAPI (Python)
│   ├── app/
│   │   ├── main.py            # FastAPI entry point
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── gemini_agent.py        # Vertex AI Agent Engine
│   │   │   ├── parallel_search.py     # Parallel Web Search MCP
│   │   │   └── prompt_templates.py    # エージェント用プロンプト
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── disclosure.py          # 開示請求書生成API
│   │   │   ├── review.py              # 審査請求書生成API
│   │   │   ├── precedent.py           # 判例・先例検索API
│   │   │   └── deadline.py            # 期限管理API
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── ordinance.py           # 条例データモデル
│   │   │   ├── request.py             # 請求書モデル
│   │   │   └── case.py                # 案件管理モデル
│   │   ├── data/
│   │   │   ├── ordinances/            # 条例データ（JSON）
│   │   │   │   ├── anjo-city.json
│   │   │   │   ├── nagoya-city.json
│   │   │   │   ├── okazaki-city.json
│   │   │   │   ├── toyota-city.json
│   │   │   │   ├── gamagori-city.json
│   │   │   │   ├── aichi-pref.json
│   │   │   │   ├── aichi-assembly.json
│   │   │   │   ├── tokyo-met.json
│   │   │   │   ├── tokyo-23wards.json
│   │   │   │   └── national.json
│   │   │   └── templates/             # テンプレート
│   │   │       ├── disclosure-request.md
│   │   │       ├── review-request.md
│   │   │       └── petition-letter.md
│   │   ├── services/
│   │   │   ├── ordinance_service.py   # 条例検索サービス
│   │   │   ├── form_generator.py      # フォーム生成
│   │   │   ├── deadline_tracker.py    # 期限追跡
│   │   │   └── precedent_searcher.py  # 判例調査
│   │   └── config.py
│   ├── tests/
│   ├── alembic/
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/                   # Next.js (TypeScript)
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx            # ホーム
│   │   ├── request/
│   │   │   └── page.tsx        # 開示請求書作成
│   │   ├── review/
│   │   │   └── page.tsx        # 審査請求書作成
│   │   ├── deadline/
│   │   │   └── page.tsx        # 期限管理ダッシュボード
│   │   ├── precedent/
│   │   │   └── page.tsx        # 判例調査
│   │   └── api/                # Next.js API Routes
│   ├── components/
│   ├── lib/
│   ├── public/
│   ├── tailwind.config.ts
│   ├── next.config.js
│   ├── tsconfig.json
│   └── package.json
│
├── infra/                      # Terraform / gcloud デプロイ
│   ├── main.tf
│   ├── cloud-run.yaml
│   ├── cloud-build.yaml
│   └── grafana-dashboard.json
│
├── docs/                       # ドキュメント
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── DEMO.md
│   ├── API.md
│   └── CONTRIBUTING.md
│
├── demo/                       # デモ動画素材
│   ├── script.md
│   └── screenshots/
│
├── assets/                     # 画像・ロゴ
│   └── logo.svg
│
├── scripts/                    # 補助スクリプト
│   ├── setup.sh
│   ├── deploy.sh
│   └── seed-data.py
│
├── tests/                      # E2Eテスト
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── LICENSE                     # MITライセンス
└ README.md
```