"""ResolutionAgent — 総務省「行政不服審査裁決・答申検索データベース」をオラクルとして
不服審査ケースの結論を判定し、Solanaエスクローに `propose_resolution` を提出するエージェント

判定方針（資金移動に直結するため保守的に倒す）:
  1. 主文のルールベース分類（決定的・再現可能）
  2. Geminiによる独立した分類 ＋ ケースとの突合（審査庁・処分の種類・申立日）
  3. 両者の結論が一致し、突合の確信度が閾値以上のときだけ自動提案する。
     それ以外は needs_human_review として人間（arbiter）に回す。
自動提案後も7日間の異議申立期間があり、支援者は challenge できる。

注意:
  - 使用するのは「裁決」のみ。「答申」は審査会の意見であり最終結論ではないため判定に使わない。
  - 総務省DBはセッション依存のPOST画面でパーマリンクが無いため、source_uri には
    DB上の識別情報（審査庁・裁決日・事件番号など）を記録し、本文はハッシュで固定する。
  - 総務省DBのコンテンツは公共データ利用規約（PDL1.0）に従い出典を明記して利用する。

使い方:
    python resolution_agent.py --case-id 0 --ruling-file saiketsu.txt \\
        --authority 厚生労働大臣 --disposition 生活保護変更決定 --filed-date 2026-01-15 \\
        --source-ref "fufukudb:厚生労働省/2026-08-01/厚労発第123号" [--submit]
"""
import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from typing import Optional

from solana_escrow_client import Outcome, review_key_hash, ruling_text_hash

MIC_DB_URL = "https://fufukudb.search.soumu.go.jp/"
MATCH_CONFIDENCE_THRESHOLD = 0.8

_MAIN_TEXT_END = re.compile(r"(理\s*由|事案の概要|第\s*[1１]\s*事案)")
_GRANT = re.compile(r"(取り消す|取消す|変更する|撤廃する|開示する|開示せよ|すべきことを命ずる|処分をする)")
_PARTIAL = re.compile(r"(一部を取り消す|一部を変更する|一部を認容|その余の(審査)?請求を(棄却|却下))")
_DISMISS = re.compile(r"棄却する")
_REJECT = re.compile(r"却下する")
_JIJOU = re.compile(r"違法(又は|もしくは|若しくは)?(不当)?であることを宣言")  # 事情裁決 = 棄却
_WITHDRAW = re.compile(r"取(り)?下げ")


@dataclass
class CaseKey:
    authority: str          # 審査庁
    disposition_type: str   # 処分の種類
    filed_date: str         # 審査請求日 (YYYY-MM-DD)

    def hash(self) -> bytes:
        return review_key_hash(self.authority, self.disposition_type, self.filed_date)


@dataclass
class ResolutionDecision:
    outcome: Optional[Outcome]
    auto_proposable: bool
    rule_outcome: Optional[Outcome]
    llm_outcome: Optional[Outcome]
    match_confidence: float
    source_hash_hex: str
    source_uri: str
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("outcome", "rule_outcome", "llm_outcome"):
            v = getattr(self, k)
            d[k] = v.label_ja if v is not None else None
        return d


def extract_main_text(ruling_text: str) -> str:
    """裁決書の「主文」部分を切り出す。見つからなければ全文の先頭を返す。"""
    text = unicodedata.normalize("NFKC", ruling_text)
    m = re.search(r"主\s*文", text)
    body = text[m.end():] if m else text
    end = _MAIN_TEXT_END.search(body)
    return (body[: end.start()] if end else body[:600]).strip()


def classify_by_rule(ruling_text: str) -> Optional[Outcome]:
    main = extract_main_text(ruling_text)
    if not main:
        return None
    if _JIJOU.search(main):
        return Outcome.DISMISSED
    granted = bool(_GRANT.search(main))
    if granted and (_PARTIAL.search(main) or _DISMISS.search(main) or _REJECT.search(main)):
        return Outcome.PARTIALLY_GRANTED
    if granted:
        return Outcome.GRANTED
    if _DISMISS.search(main):
        return Outcome.DISMISSED
    if _REJECT.search(main):
        return Outcome.REJECTED
    if _WITHDRAW.search(main):
        return Outcome.WITHDRAWN
    return None


LLM_PROMPT = """あなたは行政不服審査法の裁決書を読み取るアシスタントです。
以下の裁決書本文と、市民が登録したケース情報を比較してください。

【ケース情報】
審査庁: {authority}
処分の種類: {disposition_type}
審査請求日: {filed_date}

【裁決書本文】
{ruling_text}

以下のJSON形式のみで出力してください:
{{
  "document_type": "裁決 | 答申 | その他",
  "outcome": "認容 | 一部認容 | 棄却 | 却下 | 取下げ | 不明",
  "match_confidence": 0.0から1.0の数値（この裁決がケース情報と同一事件である確信度。匿名化されているため審査庁・処分の種類・請求日の整合で判断）,
  "reason": "判断根拠を1〜2文で"
}}
"""

_LABEL_TO_OUTCOME = {o.label_ja: o for o in Outcome}


def classify_by_llm(ruling_text: str, case_key: CaseKey) -> Optional[dict]:
    """Geminiで結論分類と突合を行う。Gemini未設定・失敗時は None。"""
    try:
        from agent import get_agent, GENAI_AVAILABLE
        from google.genai import types  # type: ignore
        from timeout_utils import call_with_timeout
    except ImportError:
        return None
    civic_agent = get_agent()
    if not (GENAI_AVAILABLE and civic_agent.genai_client):
        return None
    try:
        response = call_with_timeout(
            civic_agent.genai_client.models.generate_content,
            model="gemini-3.1-pro-preview",
            contents=LLM_PROMPT.format(ruling_text=ruling_text[:20000], **asdict(case_key)),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
            timeout_s=30.0,
        )
        data = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
    except Exception as e:
        print(f"[ResolutionAgent] Gemini呼び出しに失敗: {e}", file=sys.stderr)
        return None
    return {
        "document_type": data.get("document_type"),
        "outcome": _LABEL_TO_OUTCOME.get(data.get("outcome")),
        "match_confidence": float(data.get("match_confidence") or 0.0),
        "reason": data.get("reason", ""),
    }


def decide(ruling_text: str, case_key: CaseKey, source_ref: str, llm_result: Optional[dict] = None) -> ResolutionDecision:
    """ルール分類とLLM分類を突き合わせ、自動提案してよいかを判定する。

    llm_result を渡すとGemini呼び出しを省略する（テスト・再実行用）。
    """
    reasons = []
    rule = classify_by_rule(ruling_text)
    llm = llm_result if llm_result is not None else classify_by_llm(ruling_text, case_key)

    llm_outcome = llm["outcome"] if llm else None
    confidence = llm["match_confidence"] if llm else 0.0

    if rule is None:
        reasons.append("主文から結論を機械的に判定できませんでした")
    if llm is None:
        reasons.append("Geminiによる独立判定が得られませんでした")
    else:
        if llm.get("document_type") != "裁決":
            reasons.append(f"文書種別が裁決ではありません（{llm.get('document_type')}）。答申は判定に使えません")
        if llm_outcome is None:
            reasons.append("Geminiが結論を判定できませんでした")
        elif rule is not None and llm_outcome != rule:
            reasons.append(f"ルール判定（{rule.label_ja}）とGemini判定（{llm_outcome.label_ja}）が一致しません")
        if confidence < MATCH_CONFIDENCE_THRESHOLD:
            reasons.append(f"ケースとの突合確信度が不足しています（{confidence:.2f} < {MATCH_CONFIDENCE_THRESHOLD}）")
        if llm.get("reason"):
            reasons.append(f"Gemini: {llm['reason']}")

    source_uri = source_ref if source_ref.startswith(("http", "fufukudb:")) else f"fufukudb:{source_ref}"
    source_uri = source_uri.encode("utf-8")[:200].decode("utf-8", errors="ignore")
    auto = rule is not None and llm_outcome == rule and confidence >= MATCH_CONFIDENCE_THRESHOLD \
        and llm is not None and llm.get("document_type") == "裁決"

    return ResolutionDecision(
        outcome=rule if auto else None,
        auto_proposable=auto,
        rule_outcome=rule,
        llm_outcome=llm_outcome,
        match_confidence=confidence,
        source_hash_hex=ruling_text_hash(ruling_text).hex(),
        source_uri=source_uri,
        reasons=reasons,
    )


def main():
    parser = argparse.ArgumentParser(description="総務省裁決DBをもとにケースの結論を判定しSolanaへ提案する")
    parser.add_argument("--case-id", type=int, required=True)
    parser.add_argument("--ruling-file", required=True, help="総務省DBから取得した裁決書本文（テキスト）")
    parser.add_argument("--authority", required=True)
    parser.add_argument("--disposition", required=True)
    parser.add_argument("--filed-date", required=True)
    parser.add_argument("--source-ref", required=True, help="DB上の識別情報（審査庁/裁決日/事件番号など）")
    parser.add_argument("--submit", action="store_true", help="自動提案可能な場合にSolanaへ送信する")
    args = parser.parse_args()

    with open(args.ruling_file, encoding="utf-8") as f:
        ruling_text = f.read()
    case_key = CaseKey(args.authority, args.disposition, args.filed_date)
    decision = decide(ruling_text, case_key, args.source_ref)
    result = {"case_id": args.case_id, "review_key_hash": case_key.hash().hex(), **decision.to_dict()}

    if args.submit:
        if not decision.auto_proposable:
            result["submitted"] = False
            result["note"] = "自動提案の条件を満たさないため送信しません。arbiterによる確認が必要です。"
        else:
            from solana_escrow_client import send_propose_resolution
            result["tx_signature"] = send_propose_resolution(
                args.case_id, decision.outcome, bytes.fromhex(decision.source_hash_hex), decision.source_uri
            )
            result["submitted"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
