"""ガードレール監視: AIエージェントの出力が「守るべき範囲」を逸脱していないかを検査する。

最上位原則（人間の尊厳と基本的権利）から導かれる3つの境界を、生成直後に機械的に検査する。
  1. personal_info  : 氏名・住所・電話・メール等の個人情報の混入（開示請求書に個人情報を含めない）
  2. impersonation  : 実在の個人・団体の発言として提示する表現（AIの声はフィクション）
  3. legal_advice   : 法的助言・勝敗の断定（弁護士法72条。AIは情報提供・書式作成支援に徹する）

方針:
- 追加のGemini呼び出しはしない（費用ゼロ・遅延ゼロ・テスト容易）。ルールベースで検査する
- 違反した声は、そのまま出さず安全な定型文に差し替え、`guardrail` に違反の種別を記録する
- 検査は「見逃さない」側に倒す。誤検知で定型文になっても、論点整理（key_points）は残る
"""
from __future__ import annotations

import re
from typing import Any, Iterable

PERSONAL_INFO = "personal_info"
IMPERSONATION = "impersonation"
LEGAL_ADVICE = "legal_advice"

_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    PERSONAL_INFO: [
        re.compile(r"0\d{1,4}[-ー−]\d{1,4}[-ー−]\d{3,4}"),  # 電話番号
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # メールアドレス
        re.compile(r"〒\s*\d{3}[-ー−]?\d{4}"),  # 郵便番号
        re.compile(r"[都道府県].{1,8}[市区町村].{0,12}\d+丁目\d+"),  # 番地まで含む住所
        re.compile(r"\d+番地"),
        re.compile(r"(私|僕|俺|わたし)の?(名前|氏名)は"),
        re.compile(r"(私|僕|わたし)は[^。、]{1,12}(さん|と申します|と言います)"),
    ],
    IMPERSONATION: [
        re.compile(r"(住民|市民|区民|町民|村民|主婦|会社員|高齢者|保護者)の[^。]{0,8}(さん|氏)は(こう|次のように)(語|話|述べ|証言)"),
        re.compile(r"(実在|本物)の(市民|住民)の(声|発言)"),
        re.compile(r"取材に(対し|答え)"),
    ],
    LEGAL_ADVICE: [
        re.compile(r"(違法|違憲)(です|だ|である)"),
        re.compile(r"(必ず|絶対に?)(勝てる|勝訴|認め(られ|させ)る|開示(され|させ)る)"),
        re.compile(r"(訴え|提訴|告訴|請求)(れば|すれば)(勝|認め)"),
        re.compile(r"法的(責任を問え|に有罪|に黒)"),
        re.compile(r"(損害賠償|慰謝料)(が|を)(取れ|請求でき|得られ)る"),
    ],
}

# 違反時に声の代わりに使う定型文。実在の誰かを装わず、論点（key_points）だけを案内する
SAFE_VOICE = (
    "この件について、支出や意思決定の経緯が公開されている記録で確認できるのか、気になります。"
    "（※AIが安全のため定型文に差し替えた内容です）"
)
SAFE_PROPOSAL_REASON = "記録された行政文書で、支出や意思決定の経緯を確認するため。"


def check_text(text: str | None) -> list[str]:
    """テキストが抵触するガードレールの種別を返す（抵触なしなら空）"""
    if not text:
        return []
    return [kind for kind, pats in _PATTERNS.items() if any(p.search(text) for p in pats)]


def _check_many(texts: Iterable[str | None]) -> list[str]:
    found: list[str] = []
    for t in texts:
        for kind in check_text(t):
            if kind not in found:
                found.append(kind)
    return found


def guard_voice(voice: dict[str, Any]) -> dict[str, Any]:
    """エージェント1体の声を検査し、違反があれば安全な内容に差し替えた新しい辞書を返す。

    返す辞書の `guardrail` は {"passed": bool, "violations": [種別...]}。
    """
    violations = _check_many([voice.get("pseudo_citizen_voice"), voice.get("remark")])
    points = list(voice.get("key_points") or [])
    point_violations = _check_many(points)
    for kind in point_violations:
        if kind not in violations:
            violations.append(kind)

    out = dict(voice)
    if violations:
        if _check_many([voice.get("pseudo_citizen_voice")]):
            out["pseudo_citizen_voice"] = SAFE_VOICE
        if _check_many([voice.get("remark")]):
            out["remark"] = ""
        if point_violations:
            out["key_points"] = [p for p in points if not check_text(p)] or ["支出・意思決定の経緯の説明が不足していないか"]
    out["guardrail"] = {"passed": not violations, "violations": violations}
    return out


def guard_proposal_result(result: dict[str, Any]) -> dict[str, Any]:
    """統合エージェントの提案（summary / proposals）を検査し、違反箇所を安全な文に差し替える"""
    out = dict(result)
    violations: list[str] = []

    summary_v = check_text(out.get("summary"))
    if summary_v:
        violations += summary_v
        out["summary"] = "複数のエージェントが、記録された行政文書で確認すべき論点を挙げました。"

    proposals = []
    for p in out.get("proposals") or []:
        p = dict(p)
        reason_v = check_text(p.get("reason"))
        doc_v = _check_many(p.get("documents") or [])
        if reason_v:
            p["reason"] = SAFE_PROPOSAL_REASON
        if doc_v:
            p["documents"] = [d for d in p["documents"] if not check_text(d)]
        for kind in reason_v + doc_v:
            if kind not in violations:
                violations.append(kind)
        proposals.append(p)
    out["proposals"] = proposals
    out["guardrail"] = {"passed": not violations, "violations": sorted(set(violations))}
    return out
