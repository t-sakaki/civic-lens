"""Civic Lens — FastAPI メイン

市民の怒りを情報公開に変換するエンドポイント
"""
import os
import io
import base64
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Cookie, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import get_agent, AngerAnalysis, AgentResponse
from ordinance_data import (
    list_authorities,
    AUTHORITIES,
    ORDINANCES,
    POLICE_AUTHORITIES,
    COURT_AUTHORITIES,
    get_ordinance,
    is_court_authority,
    is_police_authority,
)
from station_guide import find_nearest_government_office, get_office_info
from emotion_analyzer import analyze_anger_from_image, anger_to_text_prompt, text_to_anger_level
from gmi_client import search_ordinances, search_precedents
from situations import get_situation_list, get_situation
from visibility import (
    create_record, update_visibility, add_result,
    get_public_records, get_my_records, get_public_stats, get_records_by_user,
    DisclosureRequestRecord,
)
from fork_star import (
    add_fork, add_star, remove_star,
    get_record_stats, get_user_actions, get_contributor_stats,
)
from web3_sbt import (
    mint_sbt, get_sbt_metadata, get_user_passport, list_available_badges
)
from auth import (
    User, register_user, authenticate_password, authenticate_wallet,
    create_session_token, verify_session_token, get_user_by_id,
    generate_siwe_nonce
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
)


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    auth_token: Optional[str] = Cookie(None),
) -> Optional[User]:
    """リクエストから認証トークンを検証し、ログイン中のユーザーを取得（未ログイン時はNone）"""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif auth_token:
        token = auth_token

    if not token:
        return None

    user_id = verify_session_token(token)
    if not user_id:
        return None
    return get_user_by_id(user_id)


from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 静的ファイル（CSS, JS）
STATIC_DIR = BASE_DIR / "static"
try:
    STATIC_DIR.mkdir(exist_ok=True)
except OSError:
    pass

if STATIC_DIR.exists():
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
    """対応機関一覧（自治体・警察・裁判所すべて）"""
    return {
        "authorities": [
            {
                "key": key,
                "name": info.authority,
                "type": info.authority_type,
                "category": info.category,
            }
            for key, info in AUTHORITIES.items()
        ]
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
    emotion_is_mock = None
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
                emotion_is_mock = analysis.is_mock
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
            is_mock=True,
        )

    return {
        "anger_analysis": anger_analysis.model_dump(),
        "emotion_data": emotion_data,
        "emotion_is_mock": emotion_is_mock,
    }


@app.post("/api/disclosure-request")
async def generate_disclosure_request(
    user_input: str = Form(...),
    target_authority: str = Form(...),
    situation_key: Optional[str] = Form(None),
    strategy_option: Optional[str] = Form("option-fast"),
):
    """開示請求書・司法行政文書開示申出書を生成（Human-in-the-loop戦略選択対応）"""
    ordinance = get_ordinance(target_authority)
    if not ordinance:
        raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")

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
        request_text, is_mock = agent.generate_disclosure_request(user_input, ordinance, strategy_option=strategy_option or "option-fast")
    except Exception as e:
        print(f"Gemini エラー: {e}")
        request_text = _mock_disclosure_request(user_input, ordinance)
        is_mock = True

    # 期限情報
    today = datetime.now()
    deadline = today + timedelta(days=ordinance.request_deadline_days)
    extended_deadline = today + timedelta(days=ordinance.request_deadline_days + ordinance.extension_days)
    review_deadline = today + timedelta(days=ordinance.review_period_days)

    is_court = getattr(ordinance, "category", "") == "裁判所"
    if is_court:
        next_steps = [
            f"1. 司法行政文書開示申出書に必要事項を記入（生成された申出書を確認・編集）",
            f"2. {ordinance.contact} に提出（窓口持参または郵送等）",
            f"3. 受付から約 {ordinance.request_deadline_days}日以内に開示決定または延長通知",
            f"4. 不開示・一部不開示決定の場合は取扱要綱に基づく苦情の申出等を検討",
        ]
    else:
        next_steps = [
            f"1. 開示請求書に必要事項を記入（生成された請求書を編集）",
            f"2. {ordinance.contact} に提出（持参・郵送・メール等）",
            f"3. 受付から約 {ordinance.request_deadline_days}日以内に決定がない場合は問い合わせ",
            f"4. 不開示決定の場合は {ordinance.review_period_days}日以内に審査請求を検討",
        ]

    return {
        "ordinance_name": ordinance.ordinance_name,
        "authority": ordinance.authority,
        "contact": ordinance.contact,
        "request_text": request_text,
        "is_mock": is_mock,
        "deadline": {
            "decision_days": ordinance.request_deadline_days,
            "decision_deadline": deadline.isoformat(),
            "extended_deadline": extended_deadline.isoformat(),
            "review_period_days": ordinance.review_period_days,
            "review_deadline": review_deadline.isoformat(),
        },
        "next_steps": next_steps,
    }


@app.post("/api/review-request")
async def generate_review_request(
    non_disclosure_decision: str = Form(...),
    target_authority: str = Form(...),
    alleged_ground: str = Form(...),
):
    """審査請求書 + 反論ロジックを生成"""
    ordinance = get_ordinance(target_authority)
    if not ordinance:
        raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")

    agent = get_agent()
    try:
        counter = agent.build_counter_argument(non_disclosure_decision, ordinance, alleged_ground)
    except Exception as e:
        print(f"Gemini エラー: {e}")
        counter = _mock_counter_argument(ordinance, alleged_ground)

    # 類似事例・判例
    precedents = search_precedents(non_disclosure_decision, top_k=5)

    return {
        "counter_argument": counter.model_dump() if hasattr(counter, "model_dump") else counter,
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
            for key, info in AUTHORITIES.items()
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
    user_id: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """新規開示請求を保存（Private/Public 選択、ログインユーザー自動紐付け）"""
    try:
        ordinance = get_ordinance(target_authority)
        if not ordinance:
            raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")

        assigned_user_id = (current_user.user_id if current_user else None) or user_id

        record = create_record(
            user_input=user_input,
            request_text=request_text,
            target_authority=target_authority,
            target_authority_name=ordinance.authority,
            visibility=visibility,
            situation_key=situation_key,
            category=ordinance.category,
            session_id=session_id,
            user_id=assigned_user_id,
        )

        return {
            "id": record.id,
            "visibility": record.visibility,
            "anonymous_user_id": record.anonymous_user_id,
            "created_at": record.created_at,
            "user_id": record.user_id,
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
    """モック開示請求書・司法行政文書開示申出書"""
    is_court = getattr(ordinance, "category", "") == "裁判所"
    doc_label = "司法行政文書" if is_court else "行政文書"
    action_verb = "申し出ます" if is_court else "請求します"
    form_title = getattr(ordinance, "request_form", "司法行政文書開示申出書" if is_court else "情報公開請求書")

    court_notice = ""
    if is_court:
        court_notice = "\n※ 個別の訴訟記録（裁判記録）ではなく、組織的運用基準・通達・公金支出等の司法行政文書を対象とします。\n"

    return f"""# {form_title}

## {ordinance.authority} {ordinance.contact} 御中

{ordinance.ordinance_name}に基づき、以下のとおり{doc_label}の開示を{action_verb}。

### 1. 請求日（申出日）
{datetime.now().strftime("%Y年%m月%d日")}

### 2. 請求人（申出人）の住所・氏名
〒000-0000 〇〇市〇〇町〇丁目〇番〇号
市民 太郎

### 3. 開示を求める{doc_label}の名称又は内容
{user_input[:200]}に関する一切の{doc_label}

### 4. 開示の方法（希望）
- [x] 写しの交付（郵送希望）
- [ ] 閲覧

### 5. 連絡先
電話：000-0000-0000
メール：example@example.com

### 6. 請求（申出）の理由・背景
{"司法行政" if is_court else "行政"}の透明性確保のため、市民として適切に情報を把握する必要があると考えるため。
{court_notice}
※ 本{"申出" if is_court else "請求"}は {ordinance.ordinance_name} に基づく正式な開示{"申出" if is_court else "請求"}です。
"""


def _mock_counter_argument(ordinance, alleged_ground: str) -> dict:
    """モック反論ロジック"""
    from ordinance_data import COURT_COUNTER_ARGUMENTS, COMMON_COUNTER_ARGUMENTS, POLICE_COUNTER_ARGUMENTS
    all_counters = {**COMMON_COUNTER_ARGUMENTS, **POLICE_COUNTER_ARGUMENTS, **COURT_COUNTER_ARGUMENTS}

    if alleged_ground in all_counters:
        counter_args = all_counters[alleged_ground]
    else:
        counter_args = [
            "不開示事由の該当性については、具体的・実質的な支障の存在を行政・裁判所側が立証する責任がある",
            "意思決定後の情報については開示すべき時期に来ている",
            "部分開示（黒塗り処理）の努力義務を怠った全面不開示決定は不当",
        ]

    is_court = getattr(ordinance, "category", "") == "裁判所"
    precedents = [
        "最高裁判所 司法行政文書開示例（裁判官会議議事録等）",
        "最判平成11年12月16日（公文書開示・意思決定後情報）",
    ] if is_court else [
        "名古屋市 海外視察費開示事例（2023）",
        "岡崎市 契約金額開示事例（2024）",
    ]

    return {
        "ground_number": alleged_ground,
        "ground_name": "不開示事由",
        "counter_arguments": counter_args,
        "precedent_cases": precedents,
        "winning_probability": 0.74,
    }


# ---------------------------------------------------------------------------
# Web3 / Civic Reputation SBT (Soulbound Token / 譲渡不能バッジNFT)
# ---------------------------------------------------------------------------

@app.post("/api/web3/sbt/mint")
async def api_mint_sbt(
    recipient_id: str = Form("市民#00001"),
    badge_key: str = Form("first_request"),
    wallet_address: Optional[str] = Form(None),
):
    """市民の開示請求・集合知貢献に対して譲渡不能SBTバッジを発行"""
    record = mint_sbt(
        recipient_id=recipient_id,
        badge_key=badge_key,
        wallet_address=wallet_address,
    )
    return record.model_dump()


@app.get("/api/web3/sbt/badges")
async def api_list_sbt_badges():
    """獲得可能なSBTバッジの一覧を取得"""
    badges = list_available_badges()
    return {"badges": badges}


@app.get("/api/web3/sbt/metadata/{token_id}")
async def api_get_sbt_metadata(token_id: str):
    """ERC-721 / ERC-5192 準拠のToken URIメタデータを取得"""
    meta = get_sbt_metadata(token_id)
    if not meta:
        raise HTTPException(404, "SBT not found")
    return meta


@app.get("/api/web3/sbt/passport/{recipient_id_or_wallet}")
async def api_get_user_passport(recipient_id_or_wallet: str):
    """特定ユーザーまたはウォレットが保有するSBT一覧（シビック・パスポート）を取得"""
    records = get_user_passport(recipient_id_or_wallet)
    return {"passport": [r.model_dump() for r in records]}


# ---------------------------------------------------------------------------
# ユーザー認証 API（メール/パスワード ＆ Web3ウォレットハイブリッド）
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
async def api_register(
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    email: Optional[str] = Form(None),
):
    """新規ユーザー登録（メール/パスワード）"""
    try:
        user = register_user(username=username, password=password, email=email)
        token = create_session_token(user.user_id)
        response.set_cookie(
            key="auth_token",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return {
            "success": True,
            "user": user.model_dump(exclude={"password_hash"}),
            "token": token,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        print(f"認証バックエンドエラー（register）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")


@app.post("/api/auth/login")
async def api_login(
    response: Response,
    username_or_email: str = Form(...),
    password: str = Form(...),
):
    """ログイン（メールまたはユーザー名 ＋ パスワード）"""
    try:
        user = authenticate_password(username_or_email=username_or_email, password=password)
    except Exception as e:
        print(f"認証バックエンドエラー（login）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")
    if not user:
        raise HTTPException(401, "ユーザー名・メールアドレスまたはパスワードが正しくありません")

    token = create_session_token(user.user_id)
    response.set_cookie(
        key="auth_token",
        value=token,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return {
        "success": True,
        "user": user.model_dump(exclude={"password_hash"}),
        "token": token,
    }


@app.get("/api/auth/nonce")
async def api_get_nonce():
    """Web3 SIWE (Sign-In with Ethereum) 用のワンタイム Nonce を取得"""
    try:
        nonce = generate_siwe_nonce()
    except Exception as e:
        print(f"認証バックエンドエラー（nonce）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")
    return {"nonce": nonce}


@app.post("/api/auth/login-wallet")
async def api_login_wallet(
    response: Response,
    wallet_address: str = Form(...),
    signature: str = Form(...),
    nonce: str = Form(...),
):
    """Web3 ウォレット（MetaMask等）によるSIWE署名ログイン"""
    if not wallet_address.startswith("0x") or len(wallet_address) != 42:
        raise HTTPException(400, "無効なEthereum/EVMウォレットアドレスです")

    try:
        user = authenticate_wallet(wallet_address=wallet_address, signature=signature, nonce=nonce)
    except ValueError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        print(f"認証バックエンドエラー（login-wallet）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")
    token = create_session_token(user.user_id)
    response.set_cookie(
        key="auth_token",
        value=token,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return {
        "success": True,
        "user": user.model_dump(exclude={"password_hash"}),
        "token": token,
    }


@app.get("/api/auth/me")
async def api_get_me(current_user: Optional[User] = Depends(get_current_user_optional)):
    """現在ログイン中のユーザー情報"""
    if not current_user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": current_user.model_dump(exclude={"password_hash"}),
    }


@app.post("/api/auth/logout")
async def api_logout(response: Response):
    """ログアウト（Cookieクリア）"""
    response.delete_cookie(key="auth_token")
    return {"success": True, "message": "ログアウトしました"}


@app.get("/api/auth/my-records")
async def api_get_my_records(current_user: Optional[User] = Depends(get_current_user_optional)):
    """ログインユーザーの保存した開示請求履歴一覧を取得"""
    if not current_user:
        raise HTTPException(401, "ログインが必要です")
    records = get_records_by_user(current_user.user_id)
    return {"records": [r.model_dump() for r in records]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)