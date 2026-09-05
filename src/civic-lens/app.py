"""Civic Lens — FastAPI メイン

市民の怒りを情報公開に変換するエンドポイント
"""
import os
import io
import base64
from typing import Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import get_agent, AngerAnalysis, AgentResponse
from ordinance_data import list_authorities, ORDINANCES, POLICE_AUTHORITIES
from station_guide import find_nearest_government_office, get_office_info
from emotion_analyzer import analyze_anger_from_image, anger_to_text_prompt, text_to_anger_level
from gmi_client import search_ordinances, search_precedents
from situations import get_situation_list, get_situation
from visibility import (
    create_record, update_visibility, add_result,
    get_public_records, get_my_records, get_public_stats,
    DisclosureRequestRecord,
)
from fork_star import (
    add_fork, add_star, remove_star,
    get_record_stats, get_user_actions, get_contributor_stats,
)


app = FastAPI(
    title="Civic Lens",
    description="市民の怒りを情報公開に変換するAIエージェント",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 静的ファイル（CSS, JS）
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class DisclosureRequest(BaseModel):
    """開示請求のリクエスト"""
    user_input: str
    target_authority: Optional[str] = None
    image_data: Optional[str] = None  # base64


@app.get("/", response_class=HTMLResponse)
async def index():
    """メインページ"""
    template_file = BASE_DIR / "templates" / "index.html"
    with open(template_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/authorities")
async def get_authorities():
    """対応自治体一覧"""
    authorities = []
    for key, info in ORDINANCES.items():
        authorities.append({
            "key": key,
            "name": info.authority,
            "type": info.authority_type,
            "category": "自治体"
        })
    for key, info in POLICE_AUTHORITIES.items():
        authorities.append({
            "key": key,
            "name": info.authority,
            "type": info.authority_type,
            "category": "警察"
        })
    return {
        "authorities": authorities
    }


@app.post("/api/analyze")
async def analyze_anger(
    user_input: str = Form(...),
    image_data: Optional[str] = Form(None),
):
    """市民の怒りを分析"""
    # 1. 感情解析（画像があれば）
    anger_level = None
    emotion_data = None
    if image_data:
        try:
            # base64デコード
            if "," in image_data:
                image_data = image_data.split(",")[1]
            image_bytes = base64.b64decode(image_data)
            analysis = analyze_anger_from_image(image_bytes)
            if analysis:
                anger_level = analysis.anger_level
                emotion_data = anger_to_text_prompt(analysis)
        except Exception as e:
            print(f"画像処理エラー: {e}")

    # 2. テキストのみの場合は簡易推定
    if anger_level is None:
        anger_level = text_to_anger_level(user_input)

    # 3. Geminiエージェントで詳細分析
    agent = get_agent()
    try:
        anger_analysis = agent.analyze_anger(user_input)
        anger_analysis.anger_level = anger_level
    except Exception as e:
        print(f"Gemini エラー: {e}")
        # フォールバック
        anger_analysis = AngerAnalysis(
            anger_level=anger_level,
            emotion_keywords=["怒り", "不信"],
            target_authority="安城市",
            target_authority_key="anjo-city",
            pain_summary=user_input[:100],
            specific_documents_requested=["（具体的な文書を Gemini 解析後に表示）"],
            legal_basis="安城市情報公開条例第7条",
            next_action="disclosure_request",
            urgency="normal",
            recommended_response_time="30日",
        )

    return {
        "anger_analysis": anger_analysis.model_dump(),
        "emotion_data": emotion_data,
    }


@app.post("/api/disclosure-request")
async def generate_disclosure_request(
    user_input: str = Form(...),
    target_authority: str = Form(...),
    situation_key: Optional[str] = Form(None),
):
    """開示請求書を生成"""
    ordinance = ORDINANCES.get(target_authority)
    if not ordinance:
        raise HTTPException(404, f"自治体が見つかりません: {target_authority}")

    # シチュエーションが指定されていれば、必要文書をマージ
    documents_to_request = None
    situation_info = None
    if situation_key:
        situation_info = get_situation(situation_key)
        if situation_info:
            documents_to_request = situation_info["documents"]
            # ユーザー入力をシチュエーションで補強
            user_input = f"{user_input}\n\n【特に請求したい文書】\n" + "\n".join(
                f"- {d}" for d in documents_to_request
            )

    agent = get_agent()
    try:
        request_text = agent.generate_disclosure_request(user_input, ordinance)
    except Exception as e:
        print(f"Gemini エラー: {e}")
        request_text = _mock_disclosure_request(user_input, ordinance)

    # 期限情報
    today = datetime.now()
    deadline = today + timedelta(days=ordinance.request_deadline_days)
    extended_deadline = today + timedelta(days=ordinance.request_deadline_days + ordinance.extension_days)
    review_deadline = today + timedelta(days=ordinance.review_period_days)

    return {
        "ordinance_name": ordinance.ordinance_name,
        "authority": ordinance.authority,
        "contact": ordinance.contact,
        "request_text": request_text,
        "deadline": {
            "decision_days": ordinance.request_deadline_days,
            "decision_deadline": deadline.isoformat(),
            "extended_deadline": extended_deadline.isoformat(),
            "review_period_days": ordinance.review_period_days,
            "review_deadline": review_deadline.isoformat(),
        },
        "next_steps": [
            f"1. 開示請求書に必要事項を記入（生成された請求書を編集）",
            f"2. {ordinance.contact} に提出（持参・郵送・メール等）",
            f"3. 受付から約 {ordinance.request_deadline_days}日以内に決定がない場合は問い合わせ",
            f"4. 不開示決定の場合は {ordinance.review_period_days}日以内に審査請求を検討",
        ],
    }


@app.post("/api/review-request")
async def generate_review_request(
    non_disclosure_decision: str = Form(...),
    target_authority: str = Form(...),
    alleged_ground: str = Form(...),
):
    """審査請求書 + 反論ロジックを生成"""
    ordinance = ORDINANCES.get(target_authority)
    if not ordinance:
        raise HTTPException(404, f"自治体が見つかりません: {target_authority}")

    agent = get_agent()
    try:
        counter = agent.build_counter_argument(non_disclosure_decision, ordinance, alleged_ground)
    except Exception as e:
        print(f"Gemini エラー: {e}")
        counter = _mock_counter_argument(ordinance, alleged_ground)

    # 類似事例・判例
    precedents = search_precedents(non_disclosure_decision, top_k=5)

    return {
        "counter_argument": counter.model_dump(),
        "precedents": [p.model_dump() for p in precedents],
        "review_authority": ordinance.review_authority,
    }


@app.post("/api/route")
async def get_route(
    target_authority: str = Form(...),
    current_lat: Optional[float] = Form(None),
    current_lon: Optional[float] = Form(None),
):
    """市役所までの経路案内"""
    office = get_office_info(target_authority)
    if current_lat is None:
        current_lat = office["lat"]
    if current_lon is None:
        current_lon = office["lon"]

    route = find_nearest_government_office(current_lat, current_lon, office["name"])

    return {
        "office": office,
        "route": route,
    }


@app.get("/api/situations")
async def get_situations():
    """シチュエーション一覧"""
    return {"situations": get_situation_list()}


@app.get("/api/situations/{key}")
async def get_situation_detail(key: str):
    """シチュエーション詳細"""
    situation = get_situation(key)
    if not situation:
        raise HTTPException(404, f"シチュエーションが見つかりません: {key}")
    return situation


@app.get("/api/ordinances")
async def get_ordinances():
    """条例一覧"""
    return {
        "ordinances": [
            {
                "key": key,
                "authority": info.authority,
                "ordinance_name": info.ordinance_name,
                "enacted": info.enacted,
                "review_authority": info.review_authority,
                "non_disclosure_grounds": [
                    {"number": g.number, "name": g.name}
                    for g in info.non_disclosure_grounds
                ],
            }
            for key, info in ORDINANCES.items()
        ]
    }


# ===========================================================================
# Public / Private 公開設定 API
# ===========================================================================

@app.post("/api/visibility/create")
async def visibility_create(
    user_input: str = Form(...),
    request_text: str = Form(...),
    target_authority: str = Form(...),
    visibility: str = Form("private"),  # "private" or "public"
    situation_key: Optional[str] = Form(None),
    category: str = Form("自治体"),
    session_id: Optional[str] = Form(None),
):
    """新規開示請求を保存（Private/Public 選択）"""
    try:
        ordinance = ORDINANCES.get(target_authority) or POLICE_AUTHORITIES.get(target_authority)
        if not ordinance:
            raise HTTPException(404, f"自治体が見つかりません: {target_authority}")

        record = create_record(
            user_input=user_input,
            request_text=request_text,
            target_authority=target_authority,
            target_authority_name=ordinance.authority,
            visibility=visibility,
            situation_key=situation_key,
            category=category if category != "自治体" else ("警察" if target_authority in POLICE_AUTHORITIES else "自治体"),
            session_id=session_id,
        )

        return {
            "id": record.id,
            "visibility": record.visibility,
            "anonymous_user_id": record.anonymous_user_id,
            "created_at": record.created_at,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/visibility/update/{record_id}")
async def visibility_update(
    record_id: str,
    visibility: str = Form(...),
    session_id: Optional[str] = Form(None),
):
    """公開設定を変更"""
    try:
        record = update_visibility(record_id, visibility, session_id)
        if not record:
            raise HTTPException(404, "Record not found or not owned by you")
        return {
            "id": record.id,
            "visibility": record.visibility,
            "anonymous_user_id": record.anonymous_user_id,
            "updated_at": record.updated_at,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/visibility/add-result/{record_id}")
async def visibility_add_result(
    record_id: str,
    result_excerpt: str = Form(...),
    session_id: Optional[str] = Form(None),
):
    """開示結果を追加"""
    record = add_result(record_id, result_excerpt, session_id)
    if not record:
        raise HTTPException(404, "Record not found or not owned by you")
    return {
        "id": record.id,
        "status": record.status,
        "result_excerpt": record.result_excerpt,
        "visibility": record.visibility,
    }


@app.get("/api/visibility/public")
async def visibility_get_public(
    category: Optional[str] = None,
    authority: Optional[str] = None,
    limit: int = 50,
):
    """公開された開示請求を取得（みんなの請求）"""
    records = get_public_records(
        category=category,
        authority=authority,
        limit=limit,
    )
    return {
        "records": [
            {
                "id": r.id,
                "anonymous_user_id": r.anonymous_user_id,
                "target_authority": r.target_authority_name,
                "category": r.category,
                "situation_key": r.situation_key,
                "user_input": r.user_input,
                "summary_public": r.summary_public,
                "tags": r.tags,
                "status": r.status,
                "result_excerpt": r.result_excerpt,
                "created_at": r.created_at,
            }
            for r in records
        ]
    }


@app.get("/api/visibility/my")
async def visibility_get_my(session_id: Optional[str] = None):
    """自分の開示請求を取得"""
    records = get_my_records(session_id=session_id)
    return {
        "records": [r.model_dump() for r in records]
    }


@app.get("/api/visibility/stats")
async def visibility_stats():
    """公開設定の統計（ダッシュボード用）"""
    return get_public_stats()


# ===========================================================================
# GitHub化機能: フォーク & スター API
# ===========================================================================

@app.post("/api/github/fork/{record_id}")
async def github_fork(
    record_id: str,
    customized_authority: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
):
    """公開請求をフォーク（参考にする）"""
    record = add_fork(
        original_record_id=record_id,
        session_id=session_id,
        customized_authority=customized_authority,
        notes=notes,
    )
    return {
        "id": record.id,
        "original_record_id": record.original_record_id,
        "forked_at": record.forked_at,
    }


@app.post("/api/github/star/{record_id}")
async def github_star(
    record_id: str,
    session_id: Optional[str] = Form(None),
):
    """公開請求にスターを追加"""
    record = add_star(record_id, session_id)
    if not record:
        raise HTTPException(400, "Already starred")
    stats = get_record_stats(record_id)
    return {
        "id": record.id,
        "record_id": record.record_id,
        "starred_at": record.starred_at,
        "stats": stats,
    }


@app.post("/api/github/unstar/{record_id}")
async def github_unstar(
    record_id: str,
    session_id: Optional[str] = Form(None),
):
    """スターを削除"""
    removed = remove_star(record_id, session_id)
    stats = get_record_stats(record_id)
    return {
        "removed": removed,
        "stats": stats,
    }


@app.get("/api/github/stats/{record_id}")
async def github_stats(record_id: str, session_id: Optional[str] = None):
    """特定レコードのスター・フォーク統計"""
    stats = get_record_stats(record_id)
    user_actions = get_user_actions(record_id, session_id)
    return {
        **stats,
        **user_actions,
    }


@app.get("/api/github/contributor")
async def github_contributor(session_id: Optional[str] = None):
    """自分のコントリビューター統計"""
    return get_contributor_stats(session_id=session_id).model_dump()


def _mock_disclosure_request(user_input: str, ordinance) -> str:
    """モック開示請求書"""
    return f"""# 情報公開請求書

## {ordinance.authority} {ordinance.contact} 御中

{ordinance.ordinance_name}に基づき、以下のとおり行政文書の開示を請求します。

### 1. 請求日
{datetime.now().strftime("%Y年%m月%d日")}

### 2. 請求人の住所・氏名
〒000-0000 〇〇市〇〇町〇丁目〇番〇号
市民 太郎

### 3. 開示請求する行政文書の名称又は内容
{user_input[:200]}に関する一切の行政文書

### 4. 開示の方法（希望）
- [x] 写しの交付（郵送希望）
- [ ] 閲覧

### 5. 連絡先
電話：000-0000-0000
メール：example@example.com

### 6. 請求の理由・背景
行政の透明性確保のため、市民として適切に情報を把握する必要があると考えるため。

※ 本請求は {ordinance.ordinance_name} に基づく正式な開示請求です。
"""


def _mock_counter_argument(ordinance, alleged_ground: str) -> dict:
    """モック反論ロジック"""
    return {
        "ground_number": alleged_ground,
        "ground_name": "法人情報",
        "counter_arguments": [
            "「法人等の正当な利益を害するおそれ」は、抽象的可能性では足りず、具体的・実質的危険性の存在が必要（最判平14.2.8）",
            "意思決定後の情報については開示すべき時期に来ている",
            "部分開示の努力义务規定（条例第11条）を懈怠した不開示決定は違法",
            "類似の開示事例が他自治体で複数あり、本件でも開示が相当",
        ],
        "precedent_cases": [
            "名古屋市 海外視察費開示事例（2023）",
            "岡崎市 契約金額開示事例（2024）",
        ],
        "winning_probability": 0.72,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)