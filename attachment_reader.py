"""添付資料の読み取り（Gemini Vision）

市民がチャットに添付した画像・PDF（行政からの通知書、決定通知、現場の写真、
スクリーンショット等）を Gemini のマルチモーダル入力で読み取り、
怒りの分析・開示請求の起案に使える形（要約・重要な事実・本文の抜き書き）にする。
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional

from pydantic import BaseModel, Field

from timeout_utils import call_with_timeout

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


class AttachmentReading(BaseModel):
    """添付資料の読み取り結果"""
    files: List[AttachmentFinding] = []
    overall_summary: str = ""
    key_facts: List[str] = Field(default_factory=list, description="日付・金額・機関名・文書番号などの重要な事実")
    extracted_text: str = Field(default="", description="資料から読み取った主要な本文の抜き書き")
    related_authority: Optional[str] = Field(default=None, description="資料から読み取れる関係機関名")
    is_mock: bool = False


READING_PROMPT = """
あなたは情報公開請求・審査請求を支援するAIです。市民が添付した資料（画像・PDF）を読み取ってください。
行政からの通知書・決定通知・請求書・現場の写真・Webページのスクリーンショットなどが想定されます。

市民のコメント: {user_input}

以下のJSON形式のみを返してください。読み取れない箇所は推測で埋めず「判読不能」と書いてください。
- files: 添付ごとの配列。各要素は {{"filename": ファイル名, "document_type": 資料の種類, "summary": 内容の要約(80文字程度)}}
  ファイル名は次の順です: {filenames}
- overall_summary: 資料全体から分かること（怒り・不満の根拠になる点を中心に、150文字程度）
- key_facts: 日付・金額・機関名・部署名・文書番号・条例の条文など、開示請求に役立つ事実のリスト（最大8件）
- extracted_text: 資料の主要な本文の抜き書き（最大800文字。写真の場合は写っている状況の説明）
- related_authority: 資料から読み取れる関係機関名（例: "安城市"、"愛知県警察本部"）。不明なら null
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
        response = call_with_timeout(
            genai_client.models.generate_content,
            model="gemini-3.1-pro-preview",
            contents=[*parts, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
            timeout_s=30.0,
        )
        data = json.loads(_strip_code_fence(response.text))
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
    if reading.extracted_text:
        lines.append(f"本文の抜き書き: {reading.extracted_text[:800]}")
    return "\n".join(lines)
