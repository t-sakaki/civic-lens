"""SDGs怒りスコアリング・エージェント（17目標×169ターゲット）

Civic Lens の理念は「怒ることに疲れた市民の代わりに、AIエージェントが行政に怒る」こと。
このモジュールは、その怒りを SDGs の17目標・169ターゲットという共通の物差しで表す。

17体の「目標エージェント」がそれぞれ、入力（市民の訴え or ニュース記事）に対して
  - 怒りスコア 0〜10
  - 怒りのコメント（AIが市民の代わりに怒る一言。実在の個人の発言ではない）
  - 関係する公式ターゲット番号（例: "16.6", "11.b"）
  - その怒りを確かめるために見るべき行政文書の観点（document_focus）
を返す。上位の目標の怒りは、行政文書開示請求の「何を・なぜ請求するか」の根拠として使う。
※ 請求する行政文書の特定ロジック（agent.py / news_anger_agent.py）自体は変更しない。
  ここでの出力は、その特定結果に添える「SDGs上の根拠」として扱う。

Gemini は gemini_models.generate（モデルの自動フォールバック）経由で呼ぶ。
Gemini 未設定・失敗時はキーワードによる簡易推定（is_mock=True）を返す。
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Optional

from gemini_models import generate as gemini_generate

# 目標番号 -> (短い名称, 番号ターゲットの最大番号, 英字ターゲット)
# 公式の 169 ターゲット（国連 2030アジェンダ）に一致する: 合計 169
SDG_GOALS: dict[int, tuple[str, int, str]] = {
    1: ("貧困をなくそう", 5, "ab"),
    2: ("飢餓をゼロに", 5, "abc"),
    3: ("すべての人に健康と福祉を", 9, "abcd"),
    4: ("質の高い教育をみんなに", 7, "abc"),
    5: ("ジェンダー平等を実現しよう", 6, "abc"),
    6: ("安全な水とトイレを世界中に", 6, "ab"),
    7: ("エネルギーをみんなに そしてクリーンに", 3, "ab"),
    8: ("働きがいも経済成長も", 10, "ab"),
    9: ("産業と技術革新の基盤をつくろう", 5, "abc"),
    10: ("人や国の不平等をなくそう", 7, "abc"),
    11: ("住み続けられるまちづくりを", 7, "abc"),
    12: ("つくる責任 つかう責任", 8, "abc"),
    13: ("気候変動に具体的な対策を", 3, "ab"),
    14: ("海の豊かさを守ろう", 7, "abc"),
    15: ("陸の豊かさも守ろう", 9, "abc"),
    16: ("平和と公正をすべての人に", 10, "ab"),
    17: ("パートナーシップで目標を達成しよう", 19, ""),
}
TOTAL_TARGETS = sum(n + len(letters) for _, n, letters in SDG_GOALS.values())  # 169

DISCLAIMER = (
    "各目標エージェントの怒りのコメントはAIが市民の代わりに生成したものであり、"
    "実在の個人の発言ではありません。ターゲット番号は国連SDGsの公式169ターゲットに基づきます。"
)

# Gemini未設定/失敗時の簡易推定用キーワード
_KEYWORDS: dict[int, list[str]] = {
    1: ["貧困", "生活保護", "困窮", "低所得", "給付", "生活費"],
    2: ["給食", "食料", "飢餓", "子ども食堂", "農業", "フードバンク"],
    3: ["病院", "医療", "介護", "健康", "事故", "感染", "保健"],
    4: ["学校", "教育", "保育", "不登校", "教員", "図書館"],
    5: ["女性", "ジェンダー", "男女", "ハラスメント", "育休"],
    6: ["水道", "下水", "トイレ", "水質", "浸水", "治水"],
    7: ["電気", "エネルギー", "再エネ", "太陽光", "電力"],
    8: ["雇用", "賃金", "労働", "非正規", "残業", "会計年度"],
    9: ["道路", "インフラ", "橋", "DX", "システム", "公共事業"],
    10: ["格差", "障害", "外国人", "高齢者", "差別", "バリアフリー"],
    11: ["再開発", "まちづくり", "防災", "交通", "住宅", "騒音", "公園"],
    12: ["ごみ", "廃棄", "リサイクル", "食品ロス", "使い捨て"],
    13: ["気候", "脱炭素", "CO2", "温暖化", "猛暑"],
    14: ["海", "漁業", "プラスチック", "沿岸", "港"],
    15: ["森林", "緑地", "生態系", "開発", "樹木", "里山", "伐採"],
    16: ["不透明", "情報公開", "随意契約", "入札", "説明", "隠", "癒着", "税金", "決裁", "黒塗り"],
    17: ["委託", "協定", "官民", "連携", "NPO", "指定管理"],
}

SCORING_PROMPT = """あなたは Civic Lens の「SDGs怒りエージェント団」です。
日本には公的なオンブズマン制度がなく、多くの市民は行政に怒ることに疲れています。
あなたたち17体の目標エージェントは、そうした市民の代わりに、SDGsの17目標と169ターゲットを物差しにして行政に怒ります。
怒りは行政文書開示請求で事実を確かめるための原動力です。記事・訴えにない事実は断定せず、「確かめたい」という形で怒ってください。

次の{kind}を読み、17目標すべてについて評価してください。
- score: その目標の観点から見た怒りの強さ 0〜10 の整数（無関係なら0）
- comment: スコアが1以上なら、市民の代わりに怒る一言（60文字程度、口語）。0なら空文字
- targets: 関係する国連SDGsの公式ターゲット番号（例: "16.6", "11.b"）。実在する番号のみ。最大3件
- document_focus: スコア3以上なら、その怒りを確かめるために開示請求で見るべき行政文書の観点（40文字程度）。それ以外は空文字
個人名・住所などの個人情報は書かないこと。

【{kind}】
{text}

以下のJSONのみを出力（説明文・コードブロック記号は不要）:
{{"goals": [{{"goal": 1, "score": 0, "comment": "", "targets": [], "document_focus": ""}}, ... 17件]}}
"""


def is_valid_target(goal: int, target: str) -> bool:
    """公式169ターゲットに存在する番号か（例: 16.6 / 11.b）"""
    if goal not in SDG_GOALS:
        return False
    m = re.fullmatch(r"(\d+)\.(\d+|[a-z])", str(target).strip())
    if not m or int(m.group(1)) != goal:
        return False
    _, max_num, letters = SDG_GOALS[goal]
    sub = m.group(2)
    return (sub.isdigit() and 1 <= int(sub) <= max_num) or (sub.isalpha() and sub in letters)


def _clamp_score(v: Any) -> int:
    try:
        return max(0, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def normalize(raw_goals: Any, is_mock: bool) -> dict:
    """モデル出力を17目標そろった形に正規化し、開示請求の根拠を組み立てる"""
    by_goal: dict[int, dict] = {}
    for g in raw_goals if isinstance(raw_goals, list) else []:
        if not isinstance(g, dict):
            continue
        try:
            n = int(g.get("goal"))
        except (TypeError, ValueError):
            continue
        if n in SDG_GOALS and n not in by_goal:
            by_goal[n] = g
    goals = []
    for n, (name, _, _) in SDG_GOALS.items():
        g = by_goal.get(n, {})
        score = _clamp_score(g.get("score", 0))
        targets = [str(t).strip() for t in (g.get("targets") or []) if is_valid_target(n, t)][:3]
        goals.append({
            "goal": n,
            "label": f"目標{n} {name}",
            "score": score,
            "comment": str(g.get("comment") or "")[:120] if score > 0 else "",
            "targets": targets if score > 0 else [],
            "document_focus": str(g.get("document_focus") or "")[:80] if score >= 3 else "",
        })
    top = sorted([g for g in goals if g["score"] > 0], key=lambda g: -g["score"])[:3]
    return {
        "goals": goals,
        "top_goals": [g["goal"] for g in top],
        "max_score": max((g["score"] for g in goals), default=0),
        "disclosure_rationale": build_disclosure_rationale(top),
        "total_targets": TOTAL_TARGETS,
        "disclaimer": DISCLAIMER,
        "is_mock": is_mock,
    }


def build_disclosure_rationale(top: list[dict]) -> str:
    """上位目標の怒りを、開示請求の「請求理由」に添えられる文章にする"""
    if not top:
        return ""
    parts = []
    for g in top:
        t = "・".join(g["targets"]) if g["targets"] else "関連ターゲット"
        focus = f"（{g['document_focus']}）" if g.get("document_focus") else ""
        parts.append(f"{g['label']}（ターゲット{t}、怒り{g['score']}/10）{focus}")
    return "SDGsの観点から、" + "、".join(parts) + "について、行政文書で事実を確認する必要がある。"


def _fallback(text: str) -> dict:
    raw = []
    for n, words in _KEYWORDS.items():
        hits = sum(1 for w in words if w in text)
        score = min(10, hits * 3)
        raw.append({
            "goal": n,
            "score": score,
            "comment": f"{SDG_GOALS[n][0]}の観点で、この件の説明はまだ足りない。" if score else "",
            "targets": [],
            "document_focus": "関連する決裁文書・支出関係書類・会議記録" if score >= 3 else "",
        })
    return normalize(raw, is_mock=True)


def _parse_json(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    return json.loads(text.strip())


def score_sdgs(text: str, kind: str = "市民の訴え", client: Optional[Any] = None, timeout_s: float = 25.0) -> dict:
    """テキストを17目標×169ターゲットで怒りスコアリングする。

    kind: "市民の訴え" または "ニュース記事"（プロンプトの文言に使う）
    client: genai.Client。未指定なら agent.get_agent().genai_client を使う
    """
    text = (text or "").strip()[:6000]
    if not text:
        return normalize([], is_mock=True)
    if client is None:
        try:
            from agent import get_agent, GENAI_AVAILABLE
            client = get_agent().genai_client if GENAI_AVAILABLE else None
        except Exception:  # noqa: BLE001
            client = None
    if client is not None:
        try:
            config = None
            try:
                from google.genai import types  # type: ignore
                config = types.GenerateContentConfig(response_mime_type="application/json")
            except Exception:  # noqa: BLE001
                pass
            resp = gemini_generate(client, "flash", SCORING_PROMPT.format(kind=kind, text=text),
                                   config=config, timeout_s=timeout_s)
            data = _parse_json(resp.text)
            goals = data.get("goals") if isinstance(data, dict) else data
            if isinstance(goals, list) and goals:
                return normalize(goals, is_mock=False)
        except Exception as e:  # noqa: BLE001
            print(f"[sdgs_scorer] Gemini呼び出しに失敗、簡易推定を使用します: {e}", file=sys.stderr)
    return _fallback(text)