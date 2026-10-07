"""Civic Lens — FastAPI メイン

市民の怒りを情報公開に変換するエンドポイント
"""
import asyncio
import time
import os
import re
import io
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
import hmac
import uuid
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import cost_guard
import voice_panel
import voice_archive
from cost_guard import rate_limit
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
    match_authority_by_text,
    list_nearby_authorities,
    find_authority_by_prefecture,
)
from station_guide import find_nearest_government_office, get_office_info
from geolocation import (
    detect_municipality,
    prefecture_code,
    list_prefectures,
    build_manual_location,
    find_nearby_municipalities,
    MunicipalityLocation,
)
from municipality_pool import get_pooled
from municipality_agent import research_municipality_now, research_prefecture_now
from municipality_history import create_record as create_municipality_history_record, get_history as get_municipality_history
from emotion_analyzer import analyze_anger_from_image, anger_to_text_prompt, text_to_anger_level
from attachment_reader import (
    ALLOWED_MIME_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    Attachment,
    build_analysis_input,
    read_attachments,
)
from gmi_client import search_ordinances, search_precedents
import civic_actions
from precedent_cases import search_cases, get_case as get_precedent_case
from situations import get_situation_list, get_situation
from visibility import (
    create_record, update_visibility, add_result,
    get_public_records, get_my_records, get_public_stats, get_records_by_user,
    get_records_by_project, get_record_by_id,
    DisclosureRequestRecord,
)
from project import (
    Project, create_project, get_project, get_projects_for_user,
    update_project, delete_project, create_invite, accept_invite,
    remove_member, update_member_role, get_user_role,
    ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER,
)
from record_comments import add_comment, get_comments
from fork_star import (
    add_fork, add_star, remove_star,
    get_record_stats, get_user_actions, get_contributor_stats,
)
from web3_bounty import (
    create_bounty, pledge_bounty, claim_bounty, get_bounty, list_all_bounties
)
from web3_chain_client import ChainClientNotConfigured
from web3_attestation import (
    issue_attestation, get_attestation, verify_attestation, list_all_attestations,
    register_wallet_attestation, scan_personal_info, PersonalInfoWarning, build_verification_kit,
)
from onchain_ledger import fetch_ledger_entries, get_ledger_entry, ledger_meta, LedgerNotConfigured
from ledger_reactions import (
    get_reactions, toggle_reaction, REACTION_TYPES as LEDGER_REACTION_TYPES,
    verify_wallet_signature, wallet_reactor_id,
)
from ledger_extensions import fetch_extensions_by_request, extension_schema_uid, EXTENSION_KINDS, MAX_REASON_SUMMARY_BYTES
from ledger_tips import build_tip_leaderboard, trending_requests, tips_for_request, tip_schema_uid
from community_feed import fetch_unified_feed, fetch_unified_stats
from web3_ipfs import (
    pin_to_ipfs, get_ipfs_record, verify_content_integrity, list_all_ipfs_records
)
from web3_sbt import (
    mint_sbt, get_sbt_metadata, get_user_passport, list_available_badges
)
from auth import (
    User, register_user, authenticate_password, authenticate_wallet,
    create_session_token, verify_session_token, get_user_by_id,
    generate_siwe_nonce, request_magic_link, verify_magic_link
)
from news_collector_agent import get_news_collector_agent
from news_anger_agent import (
    select_appearing_agents, generate_agent_voices, propose_disclosure_targets, build_proposal_input,
    PSEUDO_VOICE_DISCLAIMER, NEWS_THEMES, DEFAULT_THEME,
)
from news_reactions import (
    make_news_id, record_analysis, add_reaction, list_records, get_record,
    mark_read, count_unread, update_record,
)
from social_posting import (
    build_share_texts,
    post_to_bluesky,
    post_to_x,
    SocialPostingError,
)


app = FastAPI(
    title="Civic Lens",
    description="市民の怒りを情報公開に変換するAIエージェント",
    version="0.1.0",
)

# Cloud Run実行時は K_SERVICE が自動設定される。HTTPSで配信される本番/Cloud Run環境では
# Cookieの Secure 属性を必ず付与し、ローカルのHTTP開発サーバーでのみ省略する。
_IS_SECURE_CONTEXT = bool(os.getenv("K_SERVICE")) or os.getenv("FORCE_SECURE_COOKIES") == "1"

_cors_allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if _cors_allowed_origins:
    # 認証はCookieで行っているため、allow_credentials=Trueと
    # allow_origins=["*"]の組み合わせは、ブラウザによる制限を回避して
    # 任意オリジンから被害者のセッションでAPIを叩けてしまう脆弱な構成になる。
    # CORS_ALLOWED_ORIGINS未設定時（＝フロントエンドを同一オリジンで配信している
    # 通常構成）はクロスオリジンでのCookie送信自体を許可しない。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """未捕捉の例外をJSONで返す

    FastAPI/Starlette はデフォルトで未捕捉の例外に対し text/plain の
    "Internal Server Error" を返す。フロントエンドは基本的に res.json() で
    レスポンスをパースしているため、このプレーンテキストが
    `Unexpected token 'I', "Internal S"... is not valid JSON` という
    分かりにくいエラーとして表示されてしまう。ここでJSONに統一して、
    せめて原因が追いやすいメッセージを返す。
    """
    import traceback
    print(f"[unhandled_exception] {request.method} {request.url.path}: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "サーバー内部エラーが発生しました。しばらくしてからお試しください。"},
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


class SocialShareRequest(BaseModel):
    """疑似市民の声のSNSシェアリクエスト"""
    platform: str  # "bluesky" | "x"
    news_id: str
    access_token: Optional[str] = None  # Bluesky: app password / X: OAuth 1.0a access token
    access_token_secret: Optional[str] = None  # X のみ
    handle: Optional[str] = None  # Bluesky のユーザーhandle（例: "user.bsky.social"）
    # 記録がサーバー側に残っていない場合（Cloud Runのインスタンス切替等）のフォールバック用。
    # クライアントが画面に表示中の内容をそのまま渡す
    title: Optional[str] = None
    link: Optional[str] = None
    pseudo_citizen_voice: Optional[str] = None
    key_points: Optional[List[str]] = None


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


def _resolve_municipality_candidates(
    location: MunicipalityLocation,
    session_id: Optional[str],
    user_id: Optional[str],
) -> Dict:
    """MunicipalityLocation から対象機関候補を組み立て、履歴に保存して結果を返す

    /api/municipality/detect（GPS由来）と /api/municipality/lookup（手動選択）の
    両方から共有するロジック。lat/lon が無い（手動選択）場合は距離ベースの
    周辺候補列挙（list_nearby_authorities）をスキップする。
    """
    candidates: List[Dict] = []
    if location.lat is not None and location.lon is not None:
        candidates = list_nearby_authorities(location.lat, location.lon)

    # 都道府県（県庁）は、条例が別立てで距離に関係なく請求先になり得るため、
    # list_nearby_authorities() の半径判定とは独立して必ず候補に含める。
    pref_authority = find_authority_by_prefecture(location.prefecture)
    if pref_authority:
        if not any(c["key"] == pref_authority.key for c in candidates):
            candidates = [{
                "key": pref_authority.key,
                "name": pref_authority.authority,
                "type": pref_authority.authority_type,
                "category": pref_authority.category,
                "distance_km": None,
            }] + candidates
    else:
        pref_code = prefecture_code(location.prefecture)
        try:
            pref_pooled = get_pooled(pref_code)
        except Exception as e:
            print(f"municipality_pool 参照エラー（都道府県・Firestore未設定の可能性）: {e}")
            pref_pooled = None

        if not pref_pooled or pref_pooled.get("status") != "ready":
            try:
                pref_pooled = research_prefecture_now(pref_code, location.prefecture)
            except Exception as e:
                print(f"都道府県調査エラー: {e}")
                pref_pooled = None

        if pref_pooled and pref_pooled.get("status") == "ready":
            pref_key = f"pref:{pref_code}"
            if not any(c["key"] == pref_key for c in candidates):
                candidates = [{
                    "key": pref_key,
                    "name": pref_pooled.get("prefecture") or location.prefecture,
                    "type": pref_pooled.get("authority_type") or "知事",
                    "category": "自治体",
                    "distance_km": 0.0,
                }] + candidates

    matched_key = match_authority_by_text(location.municipality, default="")
    exact_match = AUTHORITIES[matched_key] if matched_key else None

    # 静的データに一致する市区町村があれば、距離ベースの列挙（GPS無しの手動選択では
    # スキップされる）に含まれていなくても必ず候補に加える。
    if exact_match and not any(c["key"] == matched_key for c in candidates):
        candidates = [{
            "key": matched_key,
            "name": exact_match.authority,
            "type": exact_match.authority_type,
            "category": exact_match.category,
            "distance_km": None,
        }] + candidates

    # プール参照・調査・履歴保存はFirestoreに依存する部分があるが、候補一覧（静的データの
    # みで完結）は Firebase未設定/接続失敗時でも必ず返す
    pooled = None
    research_status = None
    if not exact_match:
        try:
            pooled = get_pooled(location.muni_code)
        except Exception as e:
            print(f"municipality_pool 参照エラー（Firestore未設定の可能性）: {e}")

        if pooled and pooled.get("status") == "ready":
            research_status = "found_pooled"
        else:
            pooled = research_municipality_now(
                location.muni_code,
                location.prefecture,
                location.municipality,
                location.full_name,
                location.lat,
                location.lon,
            )
            research_status = "found_pooled" if pooled.get("status") == "ready" else "failed"

    # 調査済みプールの自治体を "pool:<muni_code>" キーの候補として先頭に追加し、
    # 対象機関プルダウンに反映できるようにする（現在地そのものなので距離0扱い）
    pool_authority_key = None
    if research_status == "found_pooled" and pooled:
        pool_authority_key = f"pool:{location.muni_code}"
        candidates = [{
            "key": pool_authority_key,
            "name": pooled.get("municipality") or location.municipality,
            "type": pooled.get("authority_type") or "市長",
            "category": "自治体",
            "distance_km": 0.0,
        }] + candidates

    try:
        create_municipality_history_record(
            muni_code=location.muni_code,
            prefecture=location.prefecture,
            municipality=location.municipality,
            full_name=location.full_name,
            lat=location.lat,
            lon=location.lon,
            status="found" if exact_match else (research_status or "researching"),
            authority_key=matched_key or pool_authority_key,
            authority_name=exact_match.authority if exact_match else (pooled.get("municipality") if pooled else None),
            candidates=[c["name"] for c in candidates],
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as e:
        print(f"municipality_history 保存エラー（Firestore未設定の可能性）: {e}")

    return {
        "status": "found" if exact_match else research_status,
        "location": location.model_dump(),
        "authority_key": matched_key or pool_authority_key,
        "authority_name": exact_match.authority if exact_match else (pooled.get("municipality") if pooled else None),
        "candidates": candidates,
        "pooled": pooled,
    }


@app.post("/api/municipality/detect")
async def detect_municipality_from_location(
    lat: float = Form(...),
    lon: float = Form(...),
    session_id: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """ブラウザの現在地（緯度経度）から対象機関の候補を複数特定する

    自治体は1つに絞り込まない。都道府県（県庁）・現在地の市区町村・周辺の市区町村・
    警察・裁判所など、現在地から一定距離内にある data/authorities/*.json 収録済みの
    機関をすべて候補として返す（`candidates`）。
    現在地の市区町村自体が未収録の場合は municipality_pool（Firestore）を確認し、
    無ければこのリクエスト内で同期的に情報公開制度を調査してプールに保存する
    （FastAPIのBackgroundTasksはVercel等のサーバーレス環境ではレスポンス送信後の
    実行が保証されず、いつまでも対象機関プルダウンに反映されない不具合があったため、
    ユーザーを待たせてでも結果を確定させてから返す方式に変更した）。
    特定結果は毎回、履歴（municipality_detection_history）にも保存する。
    """
    location = detect_municipality(lat, lon)
    if location is None:
        raise HTTPException(status_code=404, detail="現在地から自治体を特定できませんでした")

    user_id = current_user.user_id if current_user else None
    return _resolve_municipality_candidates(location, session_id, user_id)


@app.get("/api/municipality/nearby")
async def list_nearby_municipalities(lat: float, lon: float, muni_code: str = ""):
    """現在地の周辺（東西南北など複数方位）にある未調査の市区町村候補を返す

    Nominatimへの複数回の逆ジオコーディングが必要で数秒かかるため、
    /api/municipality/detect とは別に、フロントエンドが背後で追加取得する
    低優先度のエンドポイントとして設計している。周辺自治体をまとめて
    自動調査すると応答が大幅に遅延するため、ここでは発見と状態確認のみ行い、
    実際の情報公開制度の調査はユーザーが選んだ候補についてのみ
    /api/municipality/lookup で行う。
    """
    found = find_nearby_municipalities(lat, lon, exclude_muni_code=muni_code or None)

    candidates = []
    for f in found:
        matched_key = match_authority_by_text(f["municipality"], default="")
        if matched_key:
            authority = AUTHORITIES[matched_key]
            candidates.append({
                "key": matched_key,
                "name": authority.authority,
                "type": authority.authority_type,
                "category": authority.category,
                "distance_km": f["distance_km"],
                "researched": True,
            })
            continue

        try:
            pooled = get_pooled(f["muni_code"])
        except Exception as e:
            print(f"municipality_pool 参照エラー（周辺自治体・Firestore未設定の可能性）: {e}")
            pooled = None

        if pooled and pooled.get("status") == "ready":
            candidates.append({
                "key": f"pool:{f['muni_code']}",
                "name": pooled.get("municipality") or f["municipality"],
                "type": pooled.get("authority_type") or "市長",
                "category": "自治体",
                "distance_km": f["distance_km"],
                "researched": True,
            })
        else:
            # 未調査: フロントエンドで選択されたら /api/municipality/lookup で調査する
            candidates.append({
                "key": f"unresearched:{f['prefecture']}:{f['municipality']}",
                "name": f["municipality"],
                "type": "未調査",
                "category": "自治体",
                "distance_km": f["distance_km"],
                "researched": False,
                "prefecture": f["prefecture"],
                "municipality": f["municipality"],
            })

    return {"candidates": candidates}


@app.get("/api/municipality/prefectures")
async def list_prefecture_master():
    """任意選択UI用の都道府県マスタ（47件）を返す"""
    return {"prefectures": list_prefectures()}


@app.post("/api/municipality/lookup")
async def lookup_municipality_manually(
    prefecture: str = Form(...),
    municipality: str = Form(...),
    session_id: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """GPSを使わず、ユーザーが指定した都道府県・市区町村から対象機関の候補を特定する

    出張先やニュースで見た自治体など、現在地に依存せず任意の地域を対象機関に
    設定したい場合の入口。ロジックは /api/municipality/detect と共通
    （_resolve_municipality_candidates）だが、緯度経度が無いため周辺市区町村の
    距離ベース列挙は行わない。
    """
    prefecture = prefecture.strip()
    municipality = municipality.strip()
    if prefecture not in list_prefectures():
        raise HTTPException(status_code=400, detail=f"未知の都道府県です: {prefecture}")
    if not municipality:
        raise HTTPException(status_code=400, detail="市区町村名を入力してください")

    location = build_manual_location(prefecture, municipality)
    user_id = current_user.user_id if current_user else None
    return _resolve_municipality_candidates(location, session_id, user_id)


@app.get("/api/municipality/status/{muni_code}")
async def get_municipality_research_status(muni_code: str):
    """バックグラウンド調査の進捗をポーリングするためのエンドポイント"""
    try:
        pooled = get_pooled(muni_code)
    except Exception as e:
        print(f"municipality_pool 参照エラー（Firestore未設定の可能性）: {e}")
        pooled = None
    if pooled is None:
        raise HTTPException(status_code=404, detail="調査タスクが見つかりません")

    status = pooled.get("status", "researching")
    authority_key = f"pool:{muni_code}" if status == "ready" else None
    return {
        "status": status,
        "pooled": pooled,
        "authority_key": authority_key,
        "authority_name": pooled.get("municipality") if status == "ready" else None,
    }


@app.get("/api/municipality/history")
async def get_municipality_detection_history(
    session_id: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """自治体特定の実行履歴（ログイン中は user_id、未ログインは session_id で紐付け）"""
    user_id = current_user.user_id if current_user else None
    try:
        records = get_municipality_history(session_id=session_id, user_id=user_id)
    except Exception as e:
        print(f"municipality_history 参照エラー（Firestore未設定の可能性）: {e}")
        records = []
    return {"records": [r.model_dump() for r in records]}


@app.post("/api/analyze")
async def analyze_anger(
    user_input: str = Form(""),
    image_data: Optional[str] = Form(None),
    target_authority: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),
):
    """市民の怒りを分析

    target_authority: フロントエンドの対象機関<select>の現在値（Geolocationによる
    自動特定結果を含む）。請求内容から対象機関を判別できない場合のデフォルト候補として使う。
    attachments: 市民が添付した画像・PDF（通知書、現場写真など）。Gemini Visionで読み取り、
    その内容を分析の入力に加える。
    """
    user_input = user_input.strip()
    if not user_input and not image_data and not attachments:
        raise HTTPException(400, "怒りの内容を入力するか、資料を添付してください。")
    if len(attachments) > MAX_ATTACHMENTS:
        raise HTTPException(400, f"添付できるファイルは{MAX_ATTACHMENTS}件までです。")

    files: List[Attachment] = []
    for upload in attachments:
        mime_type = (upload.content_type or "").lower()
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(400, f"「{upload.filename}」は対応していない形式です（画像またはPDFを添付してください）。")
        data = await upload.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(400, f"「{upload.filename}」のサイズが大きすぎます（{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MBまで）。")
        files.append(Attachment(filename=upload.filename or "添付ファイル", mime_type=mime_type, data=data))

    # get_ordinance() は固定収録済みの機関に加え、GPSのバックグラウンド調査で判明した
    # 未収録自治体（"pool:<muni_code>" キー）も認識する。AUTHORITIES直接参照だと
    # pool:キーが常に無効扱いになり、GPSで特定した自治体のヒントが失われてしまう。
    hint_authority_key = target_authority if target_authority and get_ordinance(target_authority) else None
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

    agent = get_agent()

    # 2. 添付資料があれば Gemini Vision で読み取り、分析の入力に加える
    attachments_reading = None
    analysis_input = user_input
    if files:
        attachments_reading = await asyncio.to_thread(read_attachments, files, user_input, agent.genai_client)
        analysis_input = build_analysis_input(user_input, attachments_reading)
    if not analysis_input:
        analysis_input = "（写真のみ送信）"

    # 3. テキストのみの場合は簡易推定
    if anger_level is None:
        anger_level = text_to_anger_level(analysis_input)

    # 4. Geminiエージェントで詳細分析
    try:
        anger_analysis = agent.analyze_anger(analysis_input, hint_authority_key)
        anger_analysis.anger_level = anger_level
    except Exception as e:
        print(f"Gemini エラー: {e}")
        # フォールバック（対象機関はGeolocation等のヒントがあればそれを優先）
        fallback_key = hint_authority_key or "anjo-city"
        fallback_authority = get_ordinance(fallback_key) or AUTHORITIES["anjo-city"]
        anger_analysis = AngerAnalysis(
            anger_level=anger_level,
            emotion_keywords=["怒り", "不信"],
            target_authority=fallback_authority.authority,
            target_authority_key=fallback_key,
            pain_summary=analysis_input[:100],
            specific_documents_requested=["（具体的な文書を Gemini 解析後に表示）"],
            legal_basis=f"{fallback_authority.authority}情報公開条例第7条",
            next_action="disclosure_request",
            urgency="normal",
            recommended_response_time="30日",
            is_mock=True,
        )

    return {
        "anger_analysis": anger_analysis.model_dump(),
        "emotion_data": emotion_data,
        "emotion_is_mock": emotion_is_mock,
        "attachments_reading": attachments_reading.model_dump() if attachments_reading else None,
        "analysis_input": analysis_input,
    }


# ---------------------------------------------------------------------------
# ニュース怒り再現エージェント（news_collector_agent.py / news_anger_agent.py）
# 日本にはオンブズマン制度が存在しない。AIが市民の代わりにニュースへ怒り、
# その怒りを情報公開請求に変換することで、その機能的空白を埋めることを狙う。
# 詳細はAGENTS.mdの「ニュース怒り再現パイプライン」を参照。
# ---------------------------------------------------------------------------

@app.get("/api/news-agent/themes")
async def list_news_agent_themes():
    """怒り再現エージェント（news_anger_agent.NEWS_THEMES）の一覧。

    SDGs 17目標それぞれのエージェント（sdg1〜sdg17）と一般（行政監視）。
    同じニュースを、各エージェントが自分のSDG目標の観点から分析する（news_anger_agent.py参照）。
    """
    return {
        "default_theme": DEFAULT_THEME,
        "themes": [{"id": key, "label": value["label"]} for key, value in NEWS_THEMES.items()],
    }


@app.get("/api/news-agent/list")
async def list_news_for_region(
    region: str,
    keyword: Optional[str] = None,
    max_items: int = 8,
):
    """指定地域のニュースを複数件取得する（NewsCollectorAgent）。
    1件だけ自動選択するのではなく、ユーザーが記事を選んで分析できるようにする一覧表示用。
    ニュースは地域（と任意のキーワード）だけで収集し、テーマ（SDGs）では絞り込まない。
    SDGsごとの観点は、選んだ記事を分析する段階（/api/news-agent/analyze）で適用される。
    """
    collector = get_news_collector_agent()
    items = collector.fetch_news(
        region,
        extra_keywords=[keyword] if keyword else None,
        max_items=max(1, min(max_items, 20)),
    )
    if not items:
        raise HTTPException(
            404,
            f"'{region}' に関するニュースが見つかりませんでした。地域名やキーワードを変えてお試しください。",
        )

    return {
        "items": [
            {
                "news_id": make_news_id(item.link),
                "title": item.title,
                "link": item.link,
                "published": item.published,
                "summary": item.summary,
            }
            for item in items
        ]
    }


_APPEARANCE_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _get_appearances(news_id: str, news_text: str, region: Optional[str]) -> List[Dict[str, Any]]:
    """記事を読んで名乗り出るSDGsエージェントを返す（同じ記事は再判定しない）"""
    cached = _APPEARANCE_CACHE.get(news_id)
    if cached is None:
        cached = select_appearing_agents(news_text, region)
        _APPEARANCE_CACHE[news_id] = cached
    return cached


@app.post("/api/news-agent/appear", dependencies=[Depends(rate_limit)])
async def agents_appear_for_news(
    title: str = Form(...),
    link: str = Form(...),
    summary: str = Form(""),
    region: Optional[str] = Form(None),
):
    """ニュース1件に対し、どのSDGsエージェントが自律的に登場するかを返す。

    ユーザーがボタンを押さなくても、ニュース一覧の表示と同時に各エージェントが記事を読み、
    自分の担当観点から見過ごせない記事にだけ名乗り出て一言コメントする。
    声の詳細と開示請求の対象提案は /api/news-agent/analyze で、ニュース1件ごとにまとめて行う。
    """
    news_id = make_news_id(link)
    news_text = "\n".join([p for p in [title, summary] if p])
    appearances = await asyncio.to_thread(_get_appearances, news_id, news_text, region)
    return {"news_id": news_id, "appearances": appearances}


# メイン画面の入力欄（参考入力）に流すニュースへの怒り用。地域ごとに短時間キャッシュして、ページを開くたびにニュース取得・Gemini判定をしない
_TICKER_CACHE: Dict[str, Any] = {}
_TICKER_TTL_S = 600.0
_TICKER_MAX_NEWS = 6


async def _build_ticker_items(region: str) -> List[Dict[str, Any]]:
    collector = get_news_collector_agent()
    news = await asyncio.to_thread(collector.fetch_news, region, None, _TICKER_MAX_NEWS)

    async def _one(item) -> List[Dict[str, Any]]:
        news_id = make_news_id(item.link)
        try:
            appearances = await asyncio.to_thread(_get_appearances, news_id, item.as_text(), region)
        except Exception as e:
            print(f"[news-agent/ticker] 登場判定に失敗: {e}")
            return []
        return [
            {
                "news_id": news_id,
                "title": item.title,
                "link": item.link,
                "summary": item.summary,
                "published": item.published,
                "theme": a["theme"],
                "label": a["label"],
                "remark": a["remark"],
                "anger_level": a["anger_level"],
            }
            for a in appearances
        ]

    grouped = await asyncio.gather(*[_one(i) for i in news])
    # 同じ記事の声が連続しないよう、記事をまたいで交互に並べる
    items: List[Dict[str, Any]] = []
    for row in range(MAX_TICKER_AGENTS_PER_NEWS):
        for g in grouped:
            if row < len(g):
                items.append(g[row])
    return items


MAX_TICKER_AGENTS_PER_NEWS = 3


@app.get("/api/news-agent/ticker", dependencies=[Depends(rate_limit)])
async def news_agent_ticker(region: str, limit: int = 12):
    """メイン画面の入力欄の参考入力（プレースホルダー）用。地域の最新ニュースに、各SDGsエージェントが自律的に上げた怒りを返す。

    ユーザーの操作は不要。エージェントが記事を読んで名乗り出た一言コメントだけを返す
    （AI生成であり実在の市民の声ではない。画面側でもその旨を明示する）。
    """
    now = time.monotonic()
    cached = _TICKER_CACHE.get(region)
    if cached and now - cached[0] < _TICKER_TTL_S:
        items = cached[1]
    else:
        items = await _build_ticker_items(region)
        if items:
            _TICKER_CACHE[region] = (now, items)
    return {
        "region": region,
        "items": items[: max(1, min(limit, 30))],
        "disclaimer": "AIエージェントがニュースを読んで生成した見解であり、実在の市民の声ではありません。",
    }


def _resolve_hint_key(region: Optional[str], hint_key: Optional[str]) -> Optional[str]:
    if hint_key and get_ordinance(hint_key):
        return hint_key
    if region:
        return match_authority_by_text(region, default="") or None
    return None


def _multi_agent_analysis(
    news_id: str,
    news_text: str,
    region: Optional[str],
    hint_key: Optional[str],
    appearances: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """1ニュースについて、名乗り出た各SDGsエージェントの声を集め、統合して開示請求の対象を提案する。

    流れ: 登場判定 → 各エージェントの声（並行）→ 統合エージェントが開示請求の対象を提案
    hint_key: ユーザーが選択中の対象機関キー（GPS調査済みの未収録自治体 pool: キーも含む）。
    """
    hint = _resolve_hint_key(region, hint_key)
    if appearances is None:
        appearances = _get_appearances(news_id, news_text, region)
    voices = generate_agent_voices(news_text, region, appearances)
    proposed = propose_disclosure_targets(news_text, region, voices, hint_key=hint)
    return {"voices": voices, **proposed}


def _voices_to_record_fields(voices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """SNSシェア等の既存機能が参照する pseudo_citizen_voice / key_points には、最も怒りの強い声を使う"""
    top = max(voices, key=lambda v: v.get("anger_level", 0))
    return {"key_points": top["key_points"], "pseudo_citizen_voice": top["pseudo_citizen_voice"]}


def _run_anger_analysis(analysis_input: str, hint_key: Optional[str]) -> AngerAnalysis:
    """開示請求の分析（agent.analyze_anger）。Gemini失敗時は最低限のモック結果を返す"""
    try:
        return get_agent().analyze_anger(analysis_input, hint_authority_key=hint_key)
    except Exception as e:
        print(f"Gemini エラー: {e}")
        fallback_key = hint_key if hint_key and get_ordinance(hint_key) else "anjo-city"
        fallback_authority = (get_ordinance(fallback_key) or AUTHORITIES["anjo-city"]).authority
        return AngerAnalysis(
            anger_level=text_to_anger_level(analysis_input),
            emotion_keywords=["怒り", "不信"],
            target_authority=fallback_authority,
            target_authority_key=fallback_key,
            pain_summary=analysis_input[:100],
            specific_documents_requested=["（具体的な文書を Gemini 解析後に表示）"],
            legal_basis=f"{fallback_authority}情報公開条例第7条",
            next_action="disclosure_request",
            urgency="normal",
            recommended_response_time="30日",
            is_mock=True,
        )


@app.post("/api/news-agent/analyze", dependencies=[Depends(rate_limit)])
async def analyze_news_item(
    title: str = Form(...),
    link: str = Form(...),
    summary: str = Form(""),
    published: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    authority_key: Optional[str] = Form(None),
):
    """1ニュースに対して、名乗り出た複数のSDGsエージェントの声をまとめ、開示請求の対象を提案する。

    region: ユーザーが一覧取得時に指定した対象地域。記事本文が具体的な自治体名に
    触れていない場合でも、無関係な自治体に誤って紐づかないよう対象機関特定に使う。
    authority_key: 一覧取得時にユーザーが実際に選択していた対象機関キー（分かる場合）。

    エージェント構成（AGENTS.md参照）:
      登場判定 → 各SDGsエージェントの声（並行）→ 統合エージェントの対象提案 → 記録保存（news_reactions.py）
    開示請求の詳細分析は、ユーザーが提案を選んだ時点で /api/news-agent/select-proposal が行う。
    """
    news_text = "\n".join([p for p in [title, summary] if p])
    news_id = make_news_id(link)
    result = await asyncio.to_thread(_multi_agent_analysis, news_id, news_text, region, authority_key)
    record = record_analysis(
        news_id=news_id,
        source_news={"title": title, "link": link, "published": published},
        disclaimer=PSEUDO_VOICE_DISCLAIMER,
        anger_analysis=None,
        theme="multi",
        region=region,
        autonomous=False,
        voices=result["voices"],
        proposals=result["proposals"],
        summary=result["summary"],
        overall_anger_level=result["anger_level"],
        **_voices_to_record_fields(result["voices"]),
    )
    # 画面の表示は、音声パネルの読み上げと同じ「エージェントごとの一言」にする（保存はしない）
    return {**record, "panel_script": voice_panel.agent_lines(record)}


@app.post("/api/news-agent/voice-panel", dependencies=[Depends(rate_limit)])
async def news_voice_panel(news_id: str = Form(...), format: str = Form("opus")):
    """分析済みニュースの、名乗り出たSDGsエージェントたちの議論をエージェントごとに違う声で読み上げた音声を返す。

    既定はOpus（24kbps・Ogg）。Opusを再生できない端末（Safari等）は format=wav でWAVを受け取る。

    冒頭で「AI生成のフィクションであり実在の市民の声ではない」ことを必ず読み上げる（voice_panel.OPENING）。
    TTSが使えない・日次上限に達した場合は 503 を返し、画面側はテキスト表示のままにする。
    """
    record = get_record(news_id)
    if not record or not record.get("voices"):
        raise HTTPException(status_code=404, detail="先にこのニュースの分析を行ってください")
    client = get_agent().genai_client
    if client is None:
        raise HTTPException(status_code=503, detail="音声合成は現在利用できません（Gemini未設定）")
    try:
        wav = await asyncio.to_thread(voice_panel.synthesize_panel, client, news_id, record)
    except cost_guard.BudgetExceeded:
        raise HTTPException(status_code=503, detail="本日の音声合成の上限に達しました。テキストでご覧ください")
    except Exception as e:
        print(f"[news-agent/voice-panel] 音声合成に失敗: {e}")
        raise HTTPException(status_code=503, detail="音声合成に失敗しました。テキストでご覧ください")
    # 記録は開示請求の有無にかかわらず常に残す（既定は非公開。公開は運営者の承認が必要）。記録はOpusに圧縮して保存される
    fmt = "wav" if format == "wav" else "opus"
    try:
        entry = await asyncio.to_thread(voice_archive.save, news_id, record, wav)
        audio, mime, ext = await asyncio.to_thread(voice_archive.read_audio, entry, fmt)
        headers = {"Cache-Control": "private, max-age=3600", "X-Voice-Archive-Id": entry["archive_id"],
                   **voice_archive.download_headers(voice_archive.slug(entry), ext)}
        return Response(content=audio, media_type=mime, headers=headers)
    except Exception as e:
        # 記録に失敗しても、聴くことはできるようにWAVのまま返す
        print(f"[news-agent/voice-panel] 音声アーカイブへの記録に失敗: {e}")
    headers = {"Cache-Control": "private, max-age=3600", **voice_archive.download_headers(f"civic-lens-{news_id}", "wav")}
    return Response(content=wav, media_type="audio/wav", headers=headers)


# ---- 音声アーカイブ（人が承認したものだけ公開） ----

def _require_archive_admin(token: Optional[str]) -> None:
    expected = os.getenv("ARCHIVE_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="公開の承認機能は未設定です（ARCHIVE_ADMIN_TOKEN）")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="運営者の確認トークンが正しくありません")


def _page(name: str) -> HTMLResponse:
    with open(BASE_DIR / "templates" / name, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/voices", response_class=HTMLResponse)
async def voices_page():
    """承認済みの「AIエージェントの声」の公開アーカイブ"""
    return _page("voices.html")


@app.get("/admin/voices", response_class=HTMLResponse)
async def voices_admin_page():
    """公開の承認画面（運営者の確認トークンで操作する）"""
    return _page("voices_admin.html")


@app.get("/api/voice-archive")
async def api_voice_archive():
    return {"items": [voice_archive.public_view(e) for e in voice_archive.list_published()]}


@app.get("/api/voice-archive/{archive_id}/audio")
async def api_voice_archive_audio(
    archive_id: str, format: Optional[str] = None, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")
):
    """公開済みの音声は誰でも再生できる。未公開の音声は運営者のみ（承認前の確認用）"""
    entry = voice_archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="見つかりません")
    if entry.get("status") != voice_archive.PUBLISHED:
        _require_archive_admin(x_admin_token)
    result = await asyncio.to_thread(voice_archive.read_audio, entry, format)
    if result is None:
        raise HTTPException(status_code=404, detail="音声ファイルが見つかりません")
    audio, mime, ext = result
    return Response(
        content=audio, media_type=mime,
        headers={"Cache-Control": "public, max-age=86400", **voice_archive.download_headers(voice_archive.slug(entry), ext)},
    )


@app.get("/voices/feed.xml")
async def voices_feed(request: Request):
    """承認済みの声のポッドキャスト形式RSS"""
    from xml.sax.saxutils import escape
    from email.utils import format_datetime

    base = str(request.base_url).rstrip("/")
    items = []
    for e in voice_archive.list_published():
        src = e.get("source_news") or {}
        pub = datetime.fromisoformat(e["published_at"]) if e.get("published_at") else datetime.now(timezone.utc)
        desc = f"{e.get('disclaimer', '')} 出典: {src.get('title') or ''} {src.get('link') or ''}"
        items.append(
            "<item>"
            f"<title>{escape(src.get('title') or '無題')}</title>"
            f"<description>{escape(desc)}</description>"
            f"<guid isPermaLink=\"false\">{escape(e['archive_id'])}</guid>"
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<enclosure url=\"{escape(base)}/api/voice-archive/{escape(e['archive_id'])}/audio\" length=\"{int(e.get('audio_bytes') or 0)}\" type=\"{'audio/ogg' if e.get('audio_codec') == 'opus' else 'audio/wav'}\"/>"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Civic Lens — AIエージェントの声（フィクション）</title>"
        f"<link>{escape(base)}/voices</link>"
        "<language>ja</language>"
        "<description>ニュースを読んだAIエージェントたちの議論。実在の市民の声ではなく、AIが生成したフィクションです。運営者が確認したものだけを公開しています。</description>"
        + "".join(items) + "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/api/voice-archive/admin/list")
async def api_voice_archive_admin_list(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    _require_archive_admin(x_admin_token)
    return {"items": [{**voice_archive.public_view(e), "status": e.get("status")} for e in voice_archive.list_all()]}


@app.post("/api/voice-archive/admin/{archive_id}/publish")
async def api_voice_archive_publish(
    archive_id: str,
    confirmed: bool = Form(False),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """運営者が内容を確認したうえで公開する（confirmed=true が必須。ガードレールも再検査する）"""
    _require_archive_admin(x_admin_token)
    try:
        entry = await asyncio.to_thread(voice_archive.publish, archive_id, confirmed)
    except KeyError:
        raise HTTPException(status_code=404, detail="見つかりません")
    except voice_archive.NotConfirmed as e:
        raise HTTPException(status_code=400, detail=str(e))
    except voice_archive.GuardrailViolation as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"archive_id": archive_id, "status": entry["status"], "published_at": entry["published_at"]}


@app.post("/api/voice-archive/admin/{archive_id}/unpublish")
async def api_voice_archive_unpublish(archive_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    _require_archive_admin(x_admin_token)
    try:
        entry = await asyncio.to_thread(voice_archive.unpublish, archive_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="見つかりません")
    return {"archive_id": archive_id, "status": entry["status"]}


@app.post("/api/news-agent/select-proposal", dependencies=[Depends(rate_limit)])
async def select_disclosure_proposal(
    news_id: str = Form(...),
    proposal_index: int = Form(...),
    region: Optional[str] = Form(None),
):
    """統合エージェントの提案から1つ選び、その対象・文書で開示請求の詳細分析（根拠条例・期限など）を行う。"""
    record = get_record(news_id)
    if record is None or not record.get("proposals"):
        raise HTTPException(404, "対象の記録が見つかりませんでした。先にニュースを分析してください。")
    proposals = record["proposals"]
    if not 0 <= proposal_index < len(proposals):
        raise HTTPException(400, "選択した提案が見つかりません。")
    proposal = proposals[proposal_index]

    source = record.get("source_news", {})
    news_text = source.get("title", "")
    analysis_input = build_proposal_input(news_text, record.get("voices", []), proposal)
    analysis = await asyncio.to_thread(_run_anger_analysis, analysis_input, proposal["target_authority_key"])
    return update_record(
        news_id,
        anger_analysis=analysis.model_dump(),
        selected_proposal=proposal_index,
    )


# Cloud Scheduler等から定期実行され、ユーザー操作を待たずに自律的にニュースをスキャンし
# 「未読の怒りカード」を貯めておくためのエンドポイント。審査基準「自律性・エージェントらしさ」
# （AGENTS.md参照）に対応するループ構造の起点。
ANGER_LEVEL_AUTOSCAN_THRESHOLD = 6


@app.post("/api/news-agent/autonomous-scan")
async def autonomous_scan_news(
    regions: str,
    themes: Optional[str] = None,
    max_items_per_region: int = 3,
    scheduler_secret: Optional[str] = Header(None, alias="X-Scheduler-Secret"),
):
    """複数地域のニュースを収集し、各SDGsエージェントの声を統合して、怒りレベルが高いニュースだけを未読記録として保存する。

    regions: カンマ区切りの地域名（例: "名古屋市,安城市"）。ユーザー登録地域の代わりに
      呼び出し側（Cloud Scheduler設定）がリスト管理する想定（AGENTS.md: 専用の地理エージェントは
      別途作らない方針のため、既存の地域推定結果をCloud Scheduler側の引数として渡す運用）。
    themes: カンマ区切りの怒りエージェントID（sdg1〜sdg17。省略時は全エージェント）。
      各エージェントが記事を読んで自分の出番か判断し、名乗り出た（themesで許可された）エージェントの声を
      ニュース1件にまとめて統合分析する。
    """
    expected_secret = os.getenv("NEWS_AGENT_SCHEDULER_SECRET")
    if expected_secret and scheduler_secret != expected_secret:
        raise HTTPException(401, "スケジューラ認証に失敗しました。")

    region_list = [r.strip() for r in regions.split(",") if r.strip()]
    allowed_themes = {t.strip() for t in themes.split(",") if t.strip()} if themes else set(NEWS_THEMES)

    collector = get_news_collector_agent()
    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for region in region_list:
        try:
            items = collector.fetch_news(region, max_items=max_items_per_region)
        except Exception as e:
            errors.append({"region": region, "error": str(e)})
            continue
        for item in items:
            try:
                news_id = make_news_id(item.link)
                if get_record(news_id) is not None:
                    continue  # 既に分析済みの記事は再スキャンしない

                news_text = item.as_text()
                appearances = await asyncio.to_thread(_get_appearances, news_id, news_text, region)
                allowed = [a for a in appearances if a["theme"] in allowed_themes]
                if not allowed:
                    continue  # 許可されたエージェントの誰も名乗り出なかった記事はスキップ

                result = await asyncio.to_thread(_multi_agent_analysis, news_id, news_text, region, None, allowed)
                if result["anger_level"] < ANGER_LEVEL_AUTOSCAN_THRESHOLD:
                    continue  # 怒りレベルが低い記事はユーザーに通知するほどではないと判断しスキップ

                record = record_analysis(
                    news_id=news_id,
                    source_news={"title": item.title, "link": item.link, "published": item.published},
                    disclaimer=PSEUDO_VOICE_DISCLAIMER,
                    anger_analysis=None,
                    theme="multi",
                    region=region,
                    autonomous=True,
                    voices=result["voices"],
                    proposals=result["proposals"],
                    summary=result["summary"],
                    overall_anger_level=result["anger_level"],
                    **_voices_to_record_fields(result["voices"]),
                )
                created.append(record)
            except Exception as e:
                errors.append({"region": region, "error": str(e)})

    return {"created_count": len(created), "created": created, "errors": errors}


@app.get("/api/news-agent/unread-count")
async def get_news_agent_unread_count():
    """自律スキャンで貯まった未読の怒りカード件数（バッジ表示用）"""
    return {"unread_count": count_unread()}


@app.post("/api/news-agent/mark-read")
async def mark_news_agent_read(news_id: str = Form(...)):
    """未読の怒りカードをユーザーが見たら既読にする"""
    record = mark_read(news_id)
    if record is None:
        raise HTTPException(404, "対象の記録が見つかりませんでした。")
    return record


@app.post("/api/news-agent/react")
async def react_to_news_item(
    news_id: str = Form(...),
    reaction: str = Form("heart"),
):
    """擬似市民の声への共感リアクション（❤️等）を記録する"""
    record = add_reaction(news_id, reaction)
    if record is None:
        raise HTTPException(404, "対象の記録が見つかりませんでした。先に記事を分析してください。")
    return record


@app.get("/api/news-agent/history")
async def get_news_agent_history(limit: int = 50):
    """これまでに分析したニュースの履歴一覧（新しい順）"""
    return {"items": list_records(limit=limit)}


@app.post("/api/social/share")
async def share_pseudo_citizen_voice(payload: SocialShareRequest):
    """疑似市民の声をユーザー自身のSNSアカウントからシェアする。

    専用botアカウントは使わず、リクエストごとに渡されたユーザー自身の認証情報
    （Bluesky app password / Xのアクセストークン）でその場限りのクライアントを作り投稿する。
    トークンはサーバー側に保存しない。

    未認証（トークン未指定）の場合は投稿を行わず、手動投稿用のシェアテキストを返す。
    """
    record = get_record(payload.news_id)
    if record is None and payload.pseudo_citizen_voice:
        # 記録はJSONファイル保存のため、別インスタンスに振り分けられると見つからない。
        # 画面に表示中の内容から組み立てて、シェアを継続できるようにする
        record = {
            "news_id": payload.news_id,
            "source_news": {"title": payload.title or "", "link": payload.link or ""},
            "key_points": payload.key_points or [],
            "pseudo_citizen_voice": payload.pseudo_citizen_voice,
        }
    if record is None:
        raise HTTPException(404, "対象の記録が見つかりませんでした。先にニュースを分析してください。")

    if payload.platform not in ("bluesky", "x"):
        raise HTTPException(400, "platform は 'bluesky' または 'x' を指定してください。")

    share_texts = build_share_texts(record)

    has_credentials = bool(payload.access_token) and (
        payload.platform == "bluesky" and payload.handle
        or payload.platform == "x" and payload.access_token_secret
    )
    if not has_credentials:
        return {
            "success": False,
            "action": "manual_post_needed",
            "share_text": share_texts,
        }

    try:
        if payload.platform == "bluesky":
            result = post_to_bluesky(
                text=share_texts["bluesky"],
                handle=payload.handle,
                app_password=payload.access_token,
            )
        else:
            x_text = share_texts["x"]["posts"][0]
            result = post_to_x(
                text=x_text,
                access_token=payload.access_token,
                access_token_secret=payload.access_token_secret,
            )
    except SocialPostingError as e:
        raise HTTPException(502, str(e))

    return {
        "success": result.success,
        "post_url": result.post_url,
        "posted_text": result.posted_text,
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
    decision_type: Optional[str] = Form("non_disclosure"),
    decision_date: Optional[str] = Form(None),
):
    """審査請求書 + 反論ロジックを生成

    decision_type: non_disclosure / partial_disclosure / neither_confirm_nor_deny / document_absent
    decision_date: 決定通知を受け取った日（YYYY-MM-DD）。指定時は審査請求期限を算出する
    """
    ordinance = get_ordinance(target_authority)
    if not ordinance:
        raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")
    if decision_type not in civic_actions.DECISION_TYPES:
        decision_type = "non_disclosure"
    known_date = None
    if decision_date:
        try:
            known_date = datetime.strptime(decision_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "decision_date は YYYY-MM-DD 形式で指定してください")

    agent = get_agent()
    try:
        counter = agent.build_counter_argument(non_disclosure_decision, ordinance, alleged_ground)
    except Exception as e:
        print(f"Gemini エラー: {e}")
        counter = _mock_counter_argument(ordinance, alleged_ground)
    counter_dict = counter.model_dump() if hasattr(counter, "model_dump") else counter

    review_text, review_is_mock = civic_actions.generate_review_request_document(
        agent.genai_client,
        ordinance,
        decision_summary=non_disclosure_decision,
        decision_type=decision_type,
        alleged_ground=alleged_ground,
        counter_arguments=counter_dict.get("counter_arguments", []),
        decision_date=known_date.strftime("%Y年%m月%d日") if known_date else None,
    )
    deadline = civic_actions.review_deadline(ordinance, known_date)

    # 類似事例・判例
    precedents = search_precedents(non_disclosure_decision, top_k=5)

    return {
        "counter_argument": counter_dict,
        "precedents": [p.model_dump() for p in precedents],
        "review_authority": ordinance.review_authority,
        "review_addressee": civic_actions.review_addressee(ordinance),
        "decision_type_label": civic_actions.DECISION_TYPES[decision_type],
        "review_request_text": review_text,
        "review_request_is_mock": review_is_mock,
        "review_period_days": ordinance.review_period_days,
        "review_deadline": deadline.isoformat() if deadline else None,
    }


@app.post("/api/police-complaint")
async def generate_police_complaint(
    user_input: str = Form(...),
    target_authority: str = Form(...),
    incident_datetime: Optional[str] = Form(""),
    incident_place: Optional[str] = Form(""),
    is_direct_party: bool = Form(True),
):
    """警察組織に対する都道府県公安委員会への苦情申出書（警察法第79条）を生成"""
    ordinance = get_ordinance(target_authority)
    if not ordinance:
        raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")
    if ordinance.category != "警察":
        raise HTTPException(400, "公安委員会への苦情申出は警察組織のみ対象です")

    agent = get_agent()
    text, is_mock = civic_actions.generate_police_complaint(
        agent.genai_client,
        ordinance,
        user_input,
        incident_datetime=incident_datetime or "",
        incident_place=incident_place or "",
        is_direct_party=is_direct_party,
    )
    commission = civic_actions.public_safety_commission(ordinance)
    return {
        "commission": commission,
        "police_authority": ordinance.authority,
        "is_direct_party": is_direct_party,
        "complaint_text": text,
        "is_mock": is_mock,
        "next_steps": [
            f"1. 生成された書面の日時・場所・内容を事実に即して加筆・修正",
            f"2. {commission}（事務は{ordinance.authority}の公安委員会補佐室等が担当）へ郵送または持参",
            "3. 警察法第79条の苦情であれば、処理結果が文書で通知される" if is_direct_party
            else "3. 意見・要望として扱われるため、回答の有無は公安委員会の運用による",
            "4. 並行して関係文書の開示請求を行うと、事実関係の裏付けになる",
        ],
    }


@app.post("/api/council-question")
async def generate_council_question(
    user_input: str = Form(...),
    target_authority: str = Form(...),
    requested_documents: Optional[str] = Form(""),
):
    """開示請求と並行して、管轄議会の議員に提案する一般質問の通告書・読み上げ原稿を生成

    requested_documents: 開示請求中の文書名（改行区切り）
    """
    ordinance = get_ordinance(target_authority)
    if not ordinance:
        raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")
    if civic_actions.council_name(ordinance) is None:
        raise HTTPException(400, "裁判所は地方議会の一般質問の対象外です")

    docs = [d.strip().lstrip("-・ ").strip() for d in (requested_documents or "").splitlines() if d.strip()]
    agent = get_agent()
    return civic_actions.generate_council_questions(agent.genai_client, ordinance, user_input, docs)


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


@app.get("/precedent-cases", response_class=HTMLResponse)
async def precedent_cases_page():
    """認容事例（裁決・答申）の閲覧・検索ページ"""
    template_file = BASE_DIR / "templates" / "precedent_cases.html"
    with open(template_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/ledger", response_class=HTMLResponse)
async def ledger_page():
    """オンチェーン開示請求台帳の閲覧ページ"""
    template_file = BASE_DIR / "templates" / "ledger.html"
    with open(template_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/ledger")
async def api_ledger(
    address: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """オンチェーン（EAS）に記録された開示請求の一覧と、市民リアクションの件数。

    記録はCivic Lensのデータベースではなく、チェーン（easscan GraphQL）から直接読み出す。
    """
    try:
        entries = fetch_ledger_entries()
        meta = ledger_meta()
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"オンチェーン台帳の読み込みに失敗しました: {e}")
    if current_user:
        user_id = current_user.user_id
    elif address and re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        user_id = wallet_reactor_id(address)  # 自分の反応済み表示用（件数のみ返し、誰かは公開しない）
    else:
        user_id = None
    # 延長決定の記録は任意機能（スキーマ未設定や読み込み失敗でも台帳本体は表示する）
    try:
        extensions = fetch_extensions_by_request({e["uid"].lower(): e for e in entries})
    except Exception:
        extensions = {}
    return {
        **meta,
        "logged_in": current_user is not None,
        "entries": [
            {
                **e,
                "reactions": get_reactions(e["uid"], user_id),
                "extensions": extensions.get(e["uid"].lower(), []),
            }
            for e in entries
        ],
    }


@app.get("/api/ledger/extensions/config")
async def api_ledger_extensions_config():
    """フロントエンドがウォレットから直接、延長決定のattestationを送信するために必要な設定値。"""
    try:
        meta = ledger_meta()
        schema_uid = extension_schema_uid()
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    return {
        "extension_schema_uid": schema_uid,
        "network": meta["network"],
        "kinds": list(EXTENSION_KINDS),
        "max_reason_summary_bytes": MAX_REASON_SUMMARY_BYTES,
    }


@app.post("/api/ledger/{uid}/react")
async def api_ledger_react(
    uid: str,
    reaction: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """台帳の記録へのリアクション（👀見守る / 🙋私も知りたい / 🔁自分の自治体でも）。ログイン必須・トグル。"""
    if current_user is None:
        raise HTTPException(401, "リアクションにはログインが必要です。")
    if reaction not in LEDGER_REACTION_TYPES:
        raise HTTPException(400, f"未対応のリアクションです: {reaction}")
    try:
        entry = get_ledger_entry(uid)
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    if entry is None:
        raise HTTPException(404, "オンチェーン台帳に該当する記録がありません。")
    return {"uid": entry["uid"], "reactions": toggle_reaction(entry["uid"], current_user.user_id, reaction)}


class WalletReactionRequest(BaseModel):
    reaction: str
    address: str
    timestamp: int
    signature: str


@app.post("/api/ledger/{uid}/react-wallet")
async def api_ledger_react_wallet(uid: str, body: WalletReactionRequest):
    """ログイン不要のリアクション。ウォレットのpersonal_sign（ガス代なし・トランザクションではない）で本人性を確認する。"""
    try:
        reactor = verify_wallet_signature(uid, body.reaction, body.address, body.timestamp, body.signature)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        entry = get_ledger_entry(uid)
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    if entry is None:
        raise HTTPException(404, "オンチェーン台帳に該当する記録がありません。")
    return {"uid": entry["uid"], "reactions": toggle_reaction(entry["uid"], reactor, body.reaction)}


# ---------------------------------------------------------------------------
# 投げ銭（オンチェーン応援）— 読み取り専用。送金・記録は必ず応援者本人のウォレットが
# 直接EASコントラクトへ署名・送信し、サーバーは代理署名・仲介を一切行わない。
# ---------------------------------------------------------------------------

@app.get("/api/ledger/tips/config")
async def api_ledger_tips_config():
    """フロントエンドがウォレットから直接EASにattestationを送信するために必要な設定値。"""
    try:
        meta = ledger_meta()
        tip_schema = tip_schema_uid()
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    return {
        "eas_schema_uid": meta["schema_uid"],
        "tip_schema_uid": tip_schema,
        "network": meta["network"],
        "usdc_address": os.getenv("USDC_CONTRACT_ADDRESS", ""),
    }


@app.get("/api/ledger/tips/leaderboard")
async def api_ledger_tips_leaderboard():
    """投げ銭・紹介の応援者ランキング（チェーンから都度集計、通貨別内訳つき）。"""
    try:
        return build_tip_leaderboard()
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"投げ銭ランキングの読み込みに失敗しました: {e}")


@app.get("/api/ledger/tips/trending")
async def api_ledger_tips_trending(window_hours: int = 24, limit: int = 10):
    """急上昇中の開示請求（直近window_hours時間で投げ銭が集まっている順）。"""
    try:
        return {"requests": trending_requests(window_hours, limit)}
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"急上昇ランキングの読み込みに失敗しました: {e}")


@app.get("/api/ledger/{uid}/tips")
async def api_ledger_entry_tips(uid: str):
    """特定の開示請求に寄せられた投げ銭の一覧（応援メッセージ付き）。"""
    try:
        return {"tips": tips_for_request(uid)}
    except LedgerNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"投げ銭一覧の読み込みに失敗しました: {e}")


@app.post("/api/web3/attestation/scan-pii")
async def api_scan_pii(text: str = Form(...)):
    """自分のウォレットで直接署名するフロー用に、オンチェーンに公開する前のPIIチェックだけを行う。

    サーバー代理署名フロー（/api/web3/attestation/issue）は内部で同じチェックを行うが、
    ウォレット直接署名フローはサーバーを経由せずに送信するため、送信前に同じ警告を
    フロントエンドから呼び出せるようにする（記録そのものは行わない）。
    """
    return {"warnings": scan_personal_info(text)}


@app.post("/api/web3/attestation/register-wallet-signed")
async def api_register_wallet_attestation(
    uid: str = Form(...),
    record_id: Optional[str] = Form(None),
    title: str = Form("開示請求書"),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """請求者本人のウォレットが直接EASコントラクトに送信・署名した開示請求attestationを、
    Civic Lens の索引（台帳表示用の付帯情報）に登録する。

    サーバーは署名も送金も行わない。チェーン上のattestationを直接検証したうえで登録するのみ。
    """
    try:
        record = register_wallet_attestation(
            uid=uid,
            record_id=record_id,
            title=title,
            owner_user_id=current_user.user_id if current_user else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    return record.model_dump()


@app.get("/api/precedent-cases")
async def api_precedent_cases(
    q: str = "",
    category: str = "",
    result: str = "",
    agency_type: str = "",
    tag: str = "",
    limit: int = 500,
):
    """認容事例の一覧・検索API（総務省 行政不服審査裁決・答申検索データベース由来）

    既定ではデータ全件を返す。fufuku-news同様、フィルタ・並べ替えはクライアント側で行う想定。
    """
    cases = search_cases(
        query=q, category=category, result=result,
        agency_type=agency_type, tag=tag, limit=limit,
    )
    return {"count": len(cases), "cases": cases}


@app.get("/api/precedent-cases/{case_id}")
async def api_precedent_case_detail(case_id: str):
    """認容事例の詳細（1件）"""
    case = get_precedent_case(case_id)
    if not case:
        raise HTTPException(404, f"事例が見つかりません: {case_id}")
    return case


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
    project_id: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """新規開示請求を保存（Private/Public 選択、ログインユーザー・プロジェクト自動紐付け）"""
    try:
        ordinance = get_ordinance(target_authority)
        if not ordinance:
            raise HTTPException(404, f"対象機関が見つかりません: {target_authority}")

        assigned_user_id = (current_user.user_id if current_user else None) or user_id

        if project_id:
            if not assigned_user_id:
                raise HTTPException(401, "プロジェクトに保存するにはログインが必要です")
            project = get_project(project_id)
            if not project or get_user_role(project, assigned_user_id) not in (ROLE_OWNER, ROLE_EDITOR):
                raise HTTPException(403, "このプロジェクトに保存する権限がありません")

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
            project_id=project_id,
        )

        return {
            "id": record.id,
            "visibility": record.visibility,
            "anonymous_user_id": record.anonymous_user_id,
            "created_at": record.created_at,
            "user_id": record.user_id,
            "project_id": record.project_id,
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


@app.get("/api/community-feed")
async def community_feed(
    category: Optional[str] = None,
    authority: Optional[str] = None,
    sort: str = "new",
    limit: int = 50,
):
    """「みんなの請求」統合フィード。

    Civic LensのDBにPublic共有された開示請求と、EASチェーンに刻印された
    開示請求（オンチェーン台帳）を1本のフィードにまとめて返す。
    sort=new（新着順・既定）/ sort=ranking（人気順：DBはスター+フォーク数、
    オンチェーンは全期間の投げ銭件数）。
    """
    return {"records": fetch_unified_feed(category=category, authority=authority, sort=sort, limit=limit)}


@app.get("/api/community-feed/stats")
async def community_feed_stats():
    """統合フィードの統計サマリ（DB分 + オンチェーン分の合算）。"""
    return fetch_unified_stats()


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
# Web3 / Civic Bounty（開示請求コピー代・調査費分散型ファンディング）
# ---------------------------------------------------------------------------

@app.post("/api/web3/bounty/create")
async def api_create_bounty(
    record_id: str = Form(...),
    title: str = Form("開示請求コピー代・郵送料プール"),
    authority: str = Form("自治体"),
    target_pages: int = Form(50),
    cost_per_page_jpy: int = Form(20),
    requester_wallet: Optional[str] = Form(None),
):
    """開示請求に対するバウンティプールを作成"""
    try:
        campaign = create_bounty(
            record_id=record_id,
            title=title,
            authority=authority,
            target_pages=target_pages,
            cost_per_page_jpy=cost_per_page_jpy,
            requester_wallet=requester_wallet,
        )
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    return campaign.model_dump()


@app.post("/api/web3/bounty/pledge")
async def api_pledge_bounty(
    bounty_id_or_record_id: str = Form(...),
    wallet_address: str = Form(...),
    tx_hash: str = Form(...),
    amount_jpy: int = Form(500),
    backer_id: str = Form("市民"),
    message: str = Form("応援しています！"),
):
    """バウンティプールにコピー代・調査費を出資（マイクロプレッジ）。

    tx_hash は出資者のウォレットからエスクローアドレスへ実際に送金した
    USDC送金トランザクションのハッシュで、サーバー側でオンチェーン検証される。
    """
    try:
        campaign = pledge_bounty(
            bounty_id_or_record_id=bounty_id_or_record_id,
            wallet_address=wallet_address,
            tx_hash=tx_hash,
            amount_jpy=amount_jpy,
            backer_id=backer_id,
            message=message,
        )
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return campaign.model_dump()


@app.post("/api/web3/bounty/claim")
async def api_claim_bounty(
    bounty_id_or_record_id: str = Form(...),
    proof_document_cid: str = Form(...),
    requester_wallet: Optional[str] = Form(None),
):
    """開示原本を提示し、プール資金をアンロック（精算・受領）"""
    try:
        campaign = claim_bounty(
            bounty_id_or_record_id=bounty_id_or_record_id,
            proof_document_cid=proof_document_cid,
            requester_wallet=requester_wallet,
        )
        return campaign.model_dump()
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/web3/bounty/all")
async def api_list_bounties():
    """すべてのバウンティキャンペーン一覧を取得"""
    campaigns = list_all_bounties()
    return {"bounties": [c.model_dump() for c in campaigns]}


@app.get("/api/web3/bounty/{bounty_id_or_record_id}")
async def api_get_bounty(bounty_id_or_record_id: str):
    """特定のバウンティプール情報を取得"""
    campaign = get_bounty(bounty_id_or_record_id)
    if not campaign:
        raise HTTPException(404, "Bounty campaign not found")
    return campaign.model_dump()


# ---------------------------------------------------------------------------
# Web3 / EAS (Ethereum Attestation Service) オンチェーン存在証明（タイムスタンプ）
# ---------------------------------------------------------------------------

@app.post("/api/web3/attestation/issue")
async def api_issue_attestation(
    record_id: Optional[str] = Form(None),
    title: str = Form("開示請求書"),
    content: str = Form(...),
    authority: str = Form("自治体"),
    legal_basis: str = Form("情報公開法・各自治体情報公開条例"),
    request_type: str = Form("行政文書開示請求"),
    user_wallet_address: Optional[str] = Form(None),
    requested_documents: str = Form(""),
    publish_plaintext: bool = Form(False),
    acknowledge_warnings: bool = Form(False),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """開示請求書に対する EAS オンチェーン存在証明（タイムスタンプ）を発行

    publish_plaintext=true の場合、requested_documents（請求する公文書の特定内容）を
    平文でオンチェーンに記録する。個人情報らしき記述があれば 422 で警告を返し、
    本人が確認して acknowledge_warnings=true で再送した場合のみ記録する。
    """
    rec_id = record_id or f"req-{uuid.uuid4().hex[:8]}"
    try:
        attestation = issue_attestation(
            record_id=rec_id,
            title=title,
            content=content,
            authority=authority,
            legal_basis=legal_basis,
            request_type=request_type,
            user_wallet_address=user_wallet_address,
            requested_documents=requested_documents,
            publish_plaintext=publish_plaintext,
            acknowledge_warnings=acknowledge_warnings,
            owner_user_id=current_user.user_id if current_user else None,
        )
    except PersonalInfoWarning as e:
        raise HTTPException(422, {"message": str(e), "personal_info_warnings": e.warnings})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    # 検証キット（原本を含む）は請求者本人へのこのレスポンスでのみ返し、サーバーには保存しない
    return {**attestation.model_dump(), "verification_kit": build_verification_kit(attestation, content)}


@app.get("/api/web3/attestation/records")
async def api_list_attestations():
    """発行されたすべてのアテステーション一覧を取得"""
    try:
        records = list_all_attestations()
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    return {"attestations": [r.model_dump() for r in records]}


@app.get("/api/web3/attestation/{uid_or_record_id}")
async def api_get_attestation(uid_or_record_id: str):
    """UID または record_id からアテステーション証明書を取得（内容はチェーンから読み出す）"""
    try:
        record = get_attestation(uid_or_record_id)
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    if not record:
        raise HTTPException(404, "Attestation record not found")
    return record.model_dump()


@app.post("/api/web3/attestation/verify")
async def api_verify_attestation(
    uid_or_record_id: str = Form(...),
    content: str = Form(...),
):
    """現在の請求文書が発行済みアテステーションと改ざんなく一致するかオンチェーン検証"""
    try:
        return verify_attestation(uid_or_record_id, content)
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))


# ---------------------------------------------------------------------------
# Web3 / IPFS 永久アーカイブ & 原本証明エンドポイント
# ---------------------------------------------------------------------------

@app.post("/api/web3/ipfs/pin")
async def api_pin_to_ipfs(
    record_id: Optional[str] = Form(None),
    title: str = Form("開示請求書"),
    content: str = Form(...),
    target_authority: str = Form("自治体"),
    situation_key: Optional[str] = Form(None),
):
    """開示請求書を IPFS に刻んで永久保存し、CIDを発行"""
    rec_id = record_id or f"req-{uuid.uuid4().hex[:8]}"
    record = await pin_to_ipfs(
        record_id=rec_id,
        title=title,
        content=content,
        target_authority=target_authority,
        situation_key=situation_key,
    )
    return record.model_dump()


@app.get("/api/web3/ipfs/record/{record_id}")
async def api_get_ipfs_record(record_id: str):
    """特定の開示請求書の IPFS アーカイブ情報を取得"""
    record = get_ipfs_record(record_id)
    if not record:
        raise HTTPException(404, "IPFS record not found")
    return record.model_dump()


@app.get("/api/web3/ipfs/records")
async def api_list_ipfs_records():
    """すべての IPFS アーカイブを取得"""
    records = list_all_ipfs_records()
    return {"records": [r.model_dump() for r in records]}


# ---------------------------------------------------------------------------
# Web3 / Civic Reputation SBT (Soulbound Token / 譲渡不能バッジNFT)
# ---------------------------------------------------------------------------

@app.post("/api/web3/sbt/mint")
async def api_mint_sbt(
    recipient_id: str = Form("市民#00001"),
    badge_key: str = Form("first_request"),
    wallet_address: str = Form(...),
):
    """市民の開示請求・集合知貢献に対して、実チェーン上のEASアテステーションとして
    譲渡不能バッジを発行する"""
    try:
        record = mint_sbt(
            recipient_id=recipient_id,
            badge_key=badge_key,
            wallet_address=wallet_address,
        )
    except ChainClientNotConfigured as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
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
            secure=_IS_SECURE_CONTEXT,
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
        secure=_IS_SECURE_CONTEXT,
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
        secure=_IS_SECURE_CONTEXT,
    )
    return {
        "success": True,
        "user": user.model_dump(exclude={"password_hash"}),
        "token": token,
    }


@app.post("/api/auth/magic-link/request")
async def api_request_magic_link(email: str = Form(...)):
    """マジックリンク（パスワード不要のワンタイムログインURL）をメールで送信"""
    try:
        request_magic_link(email=email)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        print(f"認証バックエンドエラー（magic-link/request）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")
    return {"success": True, "message": "ログイン用のリンクをメールで送信しました（15分間有効）"}


@app.get("/api/auth/magic-link/verify")
async def api_verify_magic_link(token: str):
    """メールのマジックリンクを検証し、セッションを発行してトップページへリダイレクト"""
    try:
        user = verify_magic_link(token=token)
    except ValueError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        print(f"認証バックエンドエラー（magic-link/verify）: {e}")
        raise HTTPException(503, "認証サービスが一時的に利用できません。しばらくしてからお試しください。")

    session_token = create_session_token(user.user_id)
    response = RedirectResponse(url="/")
    response.set_cookie(
        key="auth_token",
        value=session_token,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_IS_SECURE_CONTEXT,
    )
    return response


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


def _require_login(current_user: Optional[User]) -> User:
    if not current_user:
        raise HTTPException(401, "ログインが必要です")
    return current_user


def _project_to_dict(project: Project) -> dict:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "description": project.description,
        "type": project.type,
        "owner_id": project.owner_id,
        "member_ids": project.member_ids,
        "member_roles": project.member_roles,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


# ---------------------------------------------------------------------------
# プロジェクト（共同作業スペース）API
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def api_list_projects(current_user: Optional[User] = Depends(get_current_user_optional)):
    """ログインユーザーが所属するプロジェクト一覧（左メニュー用）"""
    user = _require_login(current_user)
    projects = get_projects_for_user(user.user_id)
    return {"projects": [_project_to_dict(p) for p in projects]}


@app.post("/api/projects")
async def api_create_project(
    name: str = Form(...),
    description: str = Form(""),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """新規（チーム）プロジェクトを作成"""
    user = _require_login(current_user)
    try:
        project = create_project(owner_id=user.user_id, name=name, description=description, project_type="team")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _project_to_dict(project)


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, current_user: Optional[User] = Depends(get_current_user_optional)):
    """プロジェクト詳細"""
    user = _require_login(current_user)
    project = get_project(project_id)
    if not project or get_user_role(project, user.user_id) is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return _project_to_dict(project)


@app.patch("/api/projects/{project_id}")
async def api_update_project(
    project_id: str,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """プロジェクト情報を更新"""
    user = _require_login(current_user)
    try:
        project = update_project(project_id, requester_id=user.user_id, name=name, description=description, status=status)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _project_to_dict(project)


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, current_user: Optional[User] = Depends(get_current_user_optional)):
    """プロジェクトを削除（オーナーのみ・個人プロジェクトは削除不可）"""
    user = _require_login(current_user)
    try:
        delete_project(project_id, requester_id=user.user_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"success": True}


@app.get("/api/projects/{project_id}/records")
async def api_get_project_records(project_id: str, current_user: Optional[User] = Depends(get_current_user_optional)):
    """プロジェクト内の開示請求・不服審査請求一覧"""
    user = _require_login(current_user)
    project = get_project(project_id)
    if not project or get_user_role(project, user.user_id) is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    records = get_records_by_project(project_id)
    return {"records": [r.model_dump() for r in records]}


@app.post("/api/projects/{project_id}/invites")
async def api_create_project_invite(
    project_id: str,
    role: str = Form(ROLE_EDITOR),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """プロジェクトへの招待リンクを発行"""
    user = _require_login(current_user)
    try:
        invite = create_invite(project_id, invited_by=user.user_id, role=role)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return invite.model_dump()


@app.post("/api/projects/invites/{token}/accept")
async def api_accept_project_invite(token: str, current_user: Optional[User] = Depends(get_current_user_optional)):
    """招待リンクを使ってプロジェクトに参加"""
    user = _require_login(current_user)
    try:
        project = accept_invite(token, user_id=user.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _project_to_dict(project)


@app.delete("/api/projects/{project_id}/members/{member_user_id}")
async def api_remove_project_member(
    project_id: str,
    member_user_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """メンバーを除名（オーナーのみ）"""
    user = _require_login(current_user)
    try:
        project = remove_member(project_id, requester_id=user.user_id, target_user_id=member_user_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _project_to_dict(project)


@app.patch("/api/projects/{project_id}/members/{member_user_id}")
async def api_update_project_member_role(
    project_id: str,
    member_user_id: str,
    role: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """メンバーの役割を変更（オーナーのみ）"""
    user = _require_login(current_user)
    try:
        project = update_member_role(project_id, requester_id=user.user_id, target_user_id=member_user_id, new_role=role)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _project_to_dict(project)


def _can_access_record(record: DisclosureRequestRecord, user: User) -> bool:
    """レコードへのアクセス権（コメント含む）があるか判定"""
    if record.user_id == user.user_id:
        return True
    if record.project_id:
        project = get_project(record.project_id)
        if project and get_user_role(project, user.user_id) is not None:
            return True
    return False


# ---------------------------------------------------------------------------
# 開示請求・不服審査請求へのコメント（プロジェクトメンバー間の議論）
# ---------------------------------------------------------------------------

@app.get("/api/records/{record_id}/comments")
async def api_get_record_comments(record_id: str, current_user: Optional[User] = Depends(get_current_user_optional)):
    """特定レコードへのコメント一覧を取得"""
    user = _require_login(current_user)
    record = get_record_by_id(record_id)
    if not record or not _can_access_record(record, user):
        raise HTTPException(404, "レコードが見つかりません")
    comments = get_comments(record_id)
    return {"comments": [c.model_dump() for c in comments]}


@app.post("/api/records/{record_id}/comments")
async def api_add_record_comment(
    record_id: str,
    text: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """特定レコードにコメントを追加"""
    user = _require_login(current_user)
    record = get_record_by_id(record_id)
    if not record or not _can_access_record(record, user):
        raise HTTPException(404, "レコードが見つかりません")
    try:
        comment = add_comment(record_id, user_id=user.user_id, username=user.username, text=text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return comment.model_dump()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)