"""添付資料の読み取り（Gemini Vision）

市民がチャットに添付した画像・PDF（行政からの通知書、決定通知、現場の写真、
スクリーンショット等）を Gemini のマルチモーダル入力で読み取り、
怒りの分析・開示請求の起案に使える形（要約・重要な事実・本文の抜き書き）にする。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from gemini_models import generate as gemini_generate

try:
    from google.genai import types  # type: ignore
    GENAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    GENAI_AVAILABLE = False

# Vercel のリクエスト本文上限（約4.5MB）に収まるよう、フロントエンド側でも画像を縮小して送る
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/pdf",
}


class Attachment(BaseModel):
    filename: str
    mime_type: str
    data: bytes


class AttachmentFinding(BaseModel):
    filename: str
    document_type: str = Field(description="資料の種類（例: 非開示決定通知書、工事現場の写真）")
    summary: str = Field(description="資料の内容の要約")


_VALID_DECISION_TYPES = {
    "non_disclosure",
    "partial_disclosure",
    "neither_confirm_nor_deny",
    "document_absent",
}


class NoticeMeta(BaseModel):
    """不開示・一部開示・不存在決定通知書などの構造化メタ情報（個人情報を含めない）"""
    is_disclosure_decision: bool = False
    decision_type: str = Field(
        default="non_disclosure",
        description="non_disclosure / partial_disclosure / neither_confirm_nor_deny / document_absent のいずれか",
    )
    decision_date: Optional[str] = Field(
        default=None,
        description="決定年月日・通知日（YYYY-MM-DD形式）。不明なら null",
    )
    cited_articles: List[str] = Field(
        default_factory=list,
        description="行政側が引用した不開示事由・条例条文のリスト（例: 第7条第2号）",
    )
    redacted_scope_summary: str = Field(
        default="",
        description="黒塗り・不開示とされた箇所の範囲や概要（個人識別情報を除く）",
    )
    boilerplate_risk: str = Field(
        default="",
        description="紋切り型・抽象的な不開示理由にとどまっている疑いがある場合の論点メモ",
    )


class AttachmentReading(BaseModel):
    """添付資料の読み取り結果"""
    files: List[AttachmentFinding] = []
    overall_summary: str = ""
    key_facts: List[str] = Field(default_factory=list, description="日付・金額・機関名・文書番号などの重要な事実")
    extracted_text: str = Field(default="", description="資料から読み取った主要な本文の抜き書き")
    related_authority: Optional[str] = Field(default=None, description="資料から読み取れる関係機関名")
    notice_meta: Optional[NoticeMeta] = Field(default=None, description="決定通知書・黒塗り文書だった場合の構造化情報")
    is_mock: bool = False


READING_PROMPT = """
あなたは情報公開請求・審査請求を支援するAIです。市民が添付した資料（画像・PDF）を読み取ってください。
行政からの通知書・決定通知・黒塗り公文書・請求書・現場の写真・Webページのスクリーンショットなどが想定されます。
個人名・住所・電話番号などの個人情報は出力に含めないでください。

市民のコメント: {user_input}

以下のJSON形式のみを返してください。読み取れない箇所は推測で埋めず「判読不能」と書いてください。
- files: 添付ごとの配列。各要素は {{"filename": ファイル名, "document_type": 資料の種類, "summary": 内容の要約(80文字程度)}}
  ファイル名は次の順です: {filenames}
- overall_summary: 資料全体から分かること（怒り・不満の根拠になる点を中心に、150文字程度）
- key_facts: 日付・金額・機関名・部署名・文書番号・条例の条文など、開示請求に役立つ事実のリスト（最大8件）
- extracted_text: 資料の主要な本文の抜き書き（最大800文字。写真の場合は写っている状況の説明）
- related_authority: 資料から読み取れる関係機関名（例: "安城市"、"愛知県警察本部"）。不明なら null
- notice_meta: 添付が不開示決定通知書・一部開示決定通知書・存否応答拒否・文書不存在通知または黒塗り公文書である場合は以下のオブジェクト、該当しない場合は null:
  {{
    "is_disclosure_decision": true,
    "decision_type": "non_disclosure" | "partial_disclosure" | "neither_confirm_nor_deny" | "document_absent",
    "decision_date": "YYYY-MM-DD" または null（和暦は西暦に変換、不明なら null）,
    "cited_articles": ["第7条第2号", ...],
    "redacted_scope_summary": "どの部分が黒塗り・不開示か（80文字程度）",
    "boilerplate_risk": "具体的支障の説明が乏しい定型文不開示とみられる点があれば簡潔に（なければ空文字）"
  }}
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    return text.strip()


def _mock_reading(attachments: List[Attachment]) -> AttachmentReading:
    """Gemini未設定/失敗時のフォールバック。内容は読めないため、添付があった事実だけを返す"""
    return AttachmentReading(
        files=[
            AttachmentFinding(
                filename=a.filename,
                document_type="PDF" if a.mime_type == "application/pdf" else "画像",
                summary="（Gemini Vision を利用できないため、内容を読み取れませんでした）",
            )
            for a in attachments
        ],
        overall_summary="添付資料の内容を読み取れませんでした。テキストでも状況を補足してください。",
        is_mock=True,
    )


def _normalize_notice_meta(raw: Any) -> Optional[NoticeMeta]:
    if not isinstance(raw, dict):
        return None
    is_dec = bool(raw.get("is_disclosure_decision"))
    if not is_dec:
        return NoticeMeta(is_disclosure_decision=False)
    dtype = str(raw.get("decision_type") or "non_disclosure").strip()
    if dtype not in _VALID_DECISION_TYPES:
        dtype = "non_disclosure"
    date_str = raw.get("decision_date")
    norm_date: Optional[str] = None
    if isinstance(date_str, str):
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", date_str.strip())
        if m:
            candidate = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
                norm_date = candidate
            except ValueError:
                norm_date = None
    articles = raw.get("cited_articles")
    if isinstance(articles, list):
        clean_articles = [str(a).strip() for a in articles if str(a).strip()][:8]
    elif isinstance(articles, str) and articles.strip():
        clean_articles = [articles.strip()]
    else:
        clean_articles = []
    return NoticeMeta(
        is_disclosure_decision=True,
        decision_type=dtype,
        decision_date=norm_date,
        cited_articles=clean_articles,
        redacted_scope_summary=str(raw.get("redacted_scope_summary") or "").strip()[:300],
        boilerplate_risk=str(raw.get("boilerplate_risk") or "").strip()[:300],
    )


def read_attachments(attachments: List[Attachment], user_input: str, genai_client) -> AttachmentReading:
    """添付資料を Gemini Vision で読み取る"""
    if not attachments:
        return AttachmentReading()
    if not (GENAI_AVAILABLE and genai_client):
        return _mock_reading(attachments)

    try:
        parts = [types.Part.from_bytes(data=a.data, mime_type=a.mime_type) for a in attachments]
        prompt = READING_PROMPT.format(
            user_input=user_input or "（コメントなし）",
            filenames="、".join(a.filename for a in attachments),
        )
        response = gemini_generate(
            genai_client,
            "pro",
            [*parts, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
            timeout_s=30.0,
        )
        data = json.loads(_strip_code_fence(response.text))
        if isinstance(data, dict) and "notice_meta" in data:
            data["notice_meta"] = _normalize_notice_meta(data.get("notice_meta"))
        return AttachmentReading(**data)
    except Exception as e:
        print(f"[attachment_reader] Gemini Vision の読み取りに失敗、フォールバックを使用します: {e}", file=sys.stderr)
        return _mock_reading(attachments)


def build_analysis_input(user_input: str, reading: AttachmentReading) -> str:
    """市民の入力に、添付資料の読み取り結果を足して怒り分析へ渡すテキストを作る"""
    if not reading.files or reading.is_mock:
        return user_input
    lines = [user_input.strip() or "（添付資料についての相談）", "", "【添付資料の読み取り結果（Gemini Vision）】"]
    for f in reading.files:
        lines.append(f"- {f.filename}（{f.document_type}）: {f.summary}")
    if reading.overall_summary:
        lines.append(f"概要: {reading.overall_summary}")
    if reading.key_facts:
        lines.append("重要な事実: " + " / ".join(reading.key_facts))
    if reading.related_authority:
        lines.append(f"関係機関: {reading.related_authority}")
    if reading.notice_meta and reading.notice_meta.is_disclosure_decision:
        nm = reading.notice_meta
        meta_parts = [f"決定区分={nm.decision_type}"]
        if nm.decision_date:
            meta_parts.append(f"決定日={nm.decision_date}")
        if nm.cited_articles:
            meta_parts.append("引用条文=" + "・".join(nm.cited_articles))
        if nm.redacted_scope_summary:
            meta_parts.append(f"黒塗り範囲={nm.redacted_scope_summary}")
        if nm.boilerplate_risk:
            meta_parts.append(f"定型文リスク={nm.boilerplate_risk}")
        lines.append("決定通知の構造化情報: " + " / ".join(meta_parts))
    if reading.extracted_text:
        lines.append(f"本文の抜き書き: {reading.extracted_text[:800]}")
    return "\n".join(lines)
