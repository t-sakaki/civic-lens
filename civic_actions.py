"""Civic Lens — 開示請求に連なる市民行動の書面生成

情報公開請求（agent.py の generate_disclosure_request）を起点に、行政監視を
次の段階へ進めるための書面を生成する。

- 審査請求書: 不開示・一部開示・存否応答拒否・文書不存在の決定に対する不服申立て
  （行政不服審査法第19条の記載事項に沿う。裁判所は取扱要綱に基づく苦情の申出）
- 苦情申出書: 警察職員の職務執行に対する都道府県公安委員会への苦情（警察法第79条）
- 一般質問の通告書・読み上げ原稿: 開示請求と並行して、管轄議会の議員に一般質問を
  提案するための資料（通告・登壇は議員が行う）

いずれも Gemini を優先し、利用できない場合はテンプレートで生成する。
氏名・住所等の個人情報欄はプレースホルダのままとし、提出・依頼するかどうかは
必ずユーザー自身が判断する（弁護士法72条: 法的助言ではなく書式作成支援）。
"""
import json
from datetime import date, datetime, timedelta
from typing import List, Optional

from ordinance_data import AuthorityInfo, addressee_name
from timeout_utils import call_with_timeout

try:
    from google.genai import types
except ImportError:  # google-genai 未導入環境ではテンプレート生成のみ
    types = None

GEMINI_MODEL = "gemini-3.1-pro-preview"

LEGAL_NOTICE = (
    "※ 本書面はCivic Lensによる書式作成支援であり、法的助言ではありません。"
    "提出の要否・内容の最終判断はご自身で行ってください。"
)

DECISION_TYPES = {
    "non_disclosure": "不開示決定",
    "partial_disclosure": "一部開示決定",
    "neither_confirm_nor_deny": "存否応答拒否による不開示決定",
    "document_absent": "文書不存在による不開示決定",
}


# ---------------------------------------------------------------------------
# 管轄の解決（審査庁・公安委員会・議会）
# ---------------------------------------------------------------------------

def police_prefecture(info: AuthorityInfo) -> Optional[str]:
    """警察組織の都道府県名（例: 愛知県警察本部 → 愛知県、警視庁 → 東京都）"""
    if info.category != "警察":
        return None
    if info.authority == "警視庁":
        return "東京都"
    return info.authority.removesuffix("警察本部")


def public_safety_commission(info: AuthorityInfo) -> Optional[str]:
    """警察組織を管理する都道府県公安委員会名"""
    pref = police_prefecture(info)
    return f"{pref}公安委員会" if pref else None


def review_addressee(info: AuthorityInfo) -> str:
    """審査請求（裁判所は苦情の申出）の宛先

    自治体の実施機関には上級行政庁がないため処分庁自身が審査庁となる。
    警察本部長（警視総監）の処分は都道府県公安委員会が審査庁となる。
    """
    return public_safety_commission(info) or addressee_name(info)


def council_name(info: AuthorityInfo) -> Optional[str]:
    """一般質問を行う管轄議会。裁判所は地方議会の監視対象外のため None"""
    if info.category == "警察":
        return f"{police_prefecture(info)}議会"
    if info.category != "自治体":
        return None
    if info.authority_type == "議会":
        return info.authority
    return f"{info.authority}議会"


def council_respondents(info: AuthorityInfo) -> str:
    """一般質問の答弁者（想定）"""
    if info.category == "警察":
        chief = "警視総監" if info.authority == "警視庁" else "警察本部長"
        return f"{chief}・{public_safety_commission(info)}委員長"
    if info.authority_type == "議会":
        return "議長・関係部局長"
    return f"{addressee_name(info)}・担当部局長"


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def _today_text() -> str:
    return datetime.now().strftime("%Y年%m月%d日")


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    return text.strip()


def _generate(genai_client, prompt: str, json_output: bool = False) -> Optional[str]:
    """Gemini で生成。クライアント未設定・失敗時は None（呼び出し側がテンプレートで補う）"""
    if genai_client is None or types is None:
        return None
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json") if json_output else None
        response = call_with_timeout(
            genai_client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
            timeout_s=40.0,
        )
        return _strip_code_fence(response.text)
    except Exception as e:
        print(f"[civic_actions] Gemini生成エラー: {e}, テンプレートを使用します")
        return None


def review_deadline(info: AuthorityInfo, decision_known_date: Optional[date]) -> Optional[date]:
    """審査請求期限（処分があったことを知った日の翌日から起算）"""
    if decision_known_date is None:
        return None
    return decision_known_date + timedelta(days=info.review_period_days)


# ---------------------------------------------------------------------------
# 審査請求書
# ---------------------------------------------------------------------------

def generate_review_request_document(
    genai_client,
    info: AuthorityInfo,
    decision_summary: str,
    decision_type: str,
    alleged_ground: str,
    counter_arguments: List[str],
    decision_date: Optional[str] = None,
) -> tuple[str, bool]:
    """審査請求書（裁判所は苦情申出書）を生成。戻り値: (Markdown, is_mock)"""
    is_court = info.category == "裁判所"
    decision_label = DECISION_TYPES.get(decision_type, DECISION_TYPES["non_disclosure"])
    addressee = review_addressee(info)
    processing_agency = addressee_name(info)
    form_title = "司法行政文書開示に関する苦情申出書" if is_court else "審査請求書"
    decision_date_text = decision_date or "令和　年　月　日"

    if decision_type == "document_absent":
        purpose = (
            f"処分庁が{decision_date_text}付けで行った{decision_label}を取り消し、"
            "改めて対象文書を探索・特定したうえで開示するとの裁決を求める。"
        )
    elif decision_type == "partial_disclosure":
        purpose = (
            f"処分庁が{decision_date_text}付けで行った{decision_label}のうち、"
            "不開示とした部分を取り消し、当該部分を開示するとの裁決を求める。"
        )
    else:
        purpose = (
            f"処分庁が{decision_date_text}付けで行った{decision_label}を取り消し、"
            "対象文書を開示するとの裁決を求める。"
        )
    if is_court:
        purpose = f"{decision_date_text}付けの{decision_label}について、再検討のうえ開示されるよう申し出る。"

    counter_text = "\n".join(f"- {a}" for a in counter_arguments) or "- （反論ロジックを記載）"

    prompt = f"""
あなたは情報公開条例に基づく審査請求（行政不服審査法）の書式に精通した専門家AIです。
以下の情報から、提出先に適合する正式な「{form_title}」のMarkdownドラフトを作成してください。
{"裁判所の司法行政文書は行政不服審査法の対象外のため、取扱要綱に基づく『苦情の申出』の書式としてください。" if is_court else "行政不服審査法第19条第2項の記載事項（審査請求人の氏名・住所、処分の内容、処分があったことを知った年月日、審査請求の趣旨及び理由、処分庁の教示の有無及びその内容、審査請求の年月日）を必ず含めてください。"}

【宛先】{addressee}
【処分庁】{processing_agency}
【適用条例】{info.ordinance_name}
【決定の種類】{decision_label}
【決定日】{decision_date_text}
【処分庁が示した不開示理由】{alleged_ground}
【決定・請求の経緯（市民の説明）】
{decision_summary}
【審査請求の趣旨（この文言を基本に使う）】{purpose}
【審査請求の理由として使う反論ロジック】
{counter_text}

制約:
- 氏名・住所・連絡先は「（氏名）」「（住所）」等のプレースホルダのままにする
- 不開示理由の各条項について、該当しない理由・部分開示義務・公益上の開示を論じる
- 前置きや解説は書かず、書面本文のみを出力する
- 末尾に次の一文をそのまま入れる: {LEGAL_NOTICE}
"""
    text = _generate(genai_client, prompt)
    if text:
        return text, False

    if is_court:
        body = f"""# {form_title}

## {addressee} 御中

{_today_text()}

申出人 住所: （住所）
　　　 氏名: （氏名）
　　　 連絡先: （電話番号・メールアドレス）

### 1. 苦情の対象となる決定
{processing_agency}が{decision_date_text}付けで行った{decision_label}（不開示理由: {alleged_ground}）

### 2. 申出の趣旨
{purpose}

### 3. 申出の理由
{decision_summary}

{counter_text}

{LEGAL_NOTICE}
"""
        return body, True

    body = f"""# {form_title}

## {addressee} 御中

{_today_text()}

審査請求人 住所: （住所）
　　　　　 氏名: （氏名）
　　　　　 連絡先: （電話番号・メールアドレス）

次のとおり審査請求をします。

### 1. 審査請求に係る処分の内容
{processing_agency}が{decision_date_text}付けで審査請求人に対して行った、{info.ordinance_name}に基づく{decision_label}
（処分庁が示した不開示理由: {alleged_ground}）

### 2. 審査請求に係る処分があったことを知った年月日
{decision_date_text}（決定通知書を受領した日）

### 3. 審査請求の趣旨
「{purpose}」

### 4. 審査請求の理由
（1）本件の経緯
{decision_summary}

（2）不開示理由に対する反論
{counter_text}

（3）結論
以上のとおり、本件処分は{info.ordinance_name}の解釈適用を誤ったものであり、取り消されるべきである。

### 5. 処分庁の教示の有無及びその内容
（決定通知書に記載された教示の内容を転記してください）

### 6. 添付書類
- 開示決定等通知書の写し

{LEGAL_NOTICE}
"""
    return body, True


# ---------------------------------------------------------------------------
# 公安委員会への苦情申出書（警察法第79条）
# ---------------------------------------------------------------------------

def generate_police_complaint(
    genai_client,
    info: AuthorityInfo,
    user_input: str,
    incident_datetime: str = "",
    incident_place: str = "",
    is_direct_party: bool = True,
) -> tuple[str, bool]:
    """都道府県公安委員会への苦情申出書を生成。戻り値: (Markdown, is_mock)

    警察法第79条の苦情は、職務執行により直接不利益を受けた者が申し出るもの。
    当事者でない場合は「意見・要望」の書面として作成し、その旨を明記する。
    """
    commission = public_safety_commission(info)
    if commission is None:
        raise ValueError("公安委員会への苦情申出は警察組織のみ対象です")

    form_title = "苦情申出書" if is_direct_party else "意見・要望書"
    basis = (
        "警察法第79条第1項に基づき、次のとおり苦情の申出をします。"
        if is_direct_party else
        f"{info.authority}の職務執行について、{commission}の管理機能の発揮を求め、次のとおり意見・要望を申し出ます。"
    )
    when = incident_datetime or "（日時）"
    where = incident_place or "（場所）"

    prompt = f"""
あなたは警察法第79条に基づく公安委員会への苦情申出の書式に精通した専門家AIです。
以下の市民の訴えをもとに、「{commission}」宛ての「{form_title}」のMarkdownドラフトを作成してください。

【前提】{basis}
【対象の警察組織】{info.authority}
【事案の日時】{when}
【事案の場所】{where}
【市民の訴え】
{user_input}

記載事項（見出しとして順に含める）:
1. 申出者の氏名・住所・電話番号（プレースホルダのまま）
2. 苦情（意見）の対象となる職務執行の日時・場所
3. 職務執行の具体的な内容（関与した職員が分かる範囲で、推測で個人を特定しない）
4. {"申出者が受けた不利益の内容" if is_direct_party else "問題と考える理由"}
5. 求める措置（事実調査、結果の文書による通知 等）

制約:
- 事実と評価を分けて記載し、断定的な誹謗や実名の推測を含めない
- {"警察法第79条第2項に基づき、処理結果を文書で通知するよう求める旨を記載する" if is_direct_party else "警察法第79条の苦情ではなく意見・要望であることを冒頭に明記する"}
- 前置きや解説は書かず、書面本文のみを出力する
- 末尾に次の一文をそのまま入れる: {LEGAL_NOTICE}
"""
    text = _generate(genai_client, prompt)
    if text:
        return text, False

    harm_heading = "申出者が受けた不利益の内容" if is_direct_party else "問題と考える理由"
    request_clause = (
        "上記の職務執行について事実関係を調査し、必要な措置を講じたうえで、"
        "警察法第79条第2項に基づき、処理の結果を文書により通知されるよう求めます。"
        if is_direct_party else
        f"上記について事実関係を確認し、{commission}として{info.authority}に対する管理機能を発揮されるよう要望します。"
    )
    return f"""# {form_title}

## {commission} 御中

{_today_text()}

申出者 住所: （住所）
　　　 氏名: （氏名）
　　　 電話番号: （電話番号）

{basis}

### 1. 対象となる職務執行の日時・場所
- 日時: {when}
- 場所: {where}
- 組織: {info.authority}

### 2. 職務執行の具体的な内容
{user_input}

### 3. {harm_heading}
（具体的に記載してください）

### 4. 求める措置
{request_clause}

{LEGAL_NOTICE}
""", True


# ---------------------------------------------------------------------------
# 議会の一般質問（通告書 + 読み上げ原稿）
# ---------------------------------------------------------------------------

def generate_council_questions(
    genai_client,
    info: AuthorityInfo,
    user_input: str,
    requested_documents: Optional[List[str]] = None,
) -> dict:
    """管轄議会の議員に提案する一般質問の通告（3問程度）と読み上げ原稿を生成

    戻り値: {council, respondents, questions: [{title, points, respondent, intent}],
             script, cover_note, is_mock}
    """
    council = council_name(info)
    if council is None:
        raise ValueError("裁判所は地方議会の一般質問の対象外です")
    respondents = council_respondents(info)
    docs = requested_documents or []
    docs_text = "\n".join(f"- {d}" for d in docs) or "- （開示請求の対象文書）"

    cover_note = (
        f"この通告案は、{council}の議員に一般質問を提案するための資料です。"
        "一般質問の通告・登壇は議員が行います。議会ごとの通告期限・質問時間・一問一答/一括方式を確認し、"
        "議員と相談のうえ調整してください。開示請求と並行して進めることで、"
        "文書の開示を待たずに議場で執行機関の説明責任を問うことができます。"
    )

    prompt = f"""
あなたは地方議会の一般質問に精通した政策スタッフAIです。
市民の問題意識をもとに、{council}の議員が行う一般質問の「通告書」と「登壇時の読み上げ原稿」を作成してください。
あわせて、同じ論点について以下の行政文書の情報公開請求を並行して行っています。

【市民の問題意識】
{user_input}
【並行して開示請求している文書】
{docs_text}
【答弁者（想定）】{respondents}

以下のJSON形式で返してください:
- questions: 大項目3問のリスト。各要素は
  - title: 大項目の表題（通告書にそのまま書ける簡潔なもの）
  - points: 小項目（具体的な質問）2〜3個のリスト
  - respondent: 答弁を求める者
  - intent: この質問で明らかにしたいこと（議員向けメモ、1文）
- script: 登壇時の読み上げ原稿（議長への呼びかけで始め、3問を順に、約5分で読める分量。
  事実と質問を分け、執行機関の答弁を引き出す聞き方にする）

制約:
- 1問目は事実関係・経緯、2問目は意思決定過程と公文書の作成・管理・公開、3問目は再発防止や今後の方針を基本とする
- 開示請求中の文書に触れ、議場で説明を求める形にする
- 個人名や未確認の事実を断定しない
JSONのみを出力してください。
"""
    text = _generate(genai_client, prompt, json_output=True)
    if text:
        try:
            data = json.loads(text)
            questions = [
                {
                    "title": str(q.get("title", "")),
                    "points": [str(p) for p in q.get("points", [])],
                    "respondent": str(q.get("respondent", respondents)),
                    "intent": str(q.get("intent", "")),
                }
                for q in data.get("questions", [])
                if isinstance(q, dict)
            ]
            if questions and data.get("script"):
                return {
                    "council": council,
                    "respondents": respondents,
                    "questions": questions,
                    "script": str(data["script"]),
                    "cover_note": cover_note,
                    "is_mock": False,
                }
        except (json.JSONDecodeError, AttributeError) as e:
            print(f"[civic_actions] 一般質問JSONの解析に失敗: {e}, テンプレートを使用します")

    subject = user_input.strip().replace("\n", " ")[:80]
    questions = [
        {
            "title": f"{subject}に関する事実関係と経緯について",
            "points": [
                "本件の経緯と現時点で把握している事実関係を伺う",
                "本件に要した費用の総額と内訳、財源を伺う",
            ],
            "respondent": respondents,
            "intent": "執行機関が把握している事実と数字を議事録に残す",
        },
        {
            "title": "意思決定過程と公文書の作成・管理・公開について",
            "points": [
                "本件の意思決定に至る決裁・協議の記録の有無と保存状況を伺う",
                f"市民から開示請求されている文書（{'、'.join(docs) if docs else '関係文書'}）の公開に対する見解を伺う",
            ],
            "respondent": respondents,
            "intent": "開示請求と並行して、記録の存在と公開姿勢を議場で確認する",
        },
        {
            "title": "再発防止と今後の方針について",
            "points": [
                "本件を踏まえた検証の実施予定と、結果の公表方法を伺う",
                "同種事案の説明責任を果たすための今後の方針を伺う",
            ],
            "respondent": respondents,
            "intent": "具体的な改善策と公表の約束を引き出す",
        },
    ]
    lines = ["議長のお許しをいただきましたので、通告に従い一般質問を行います。", ""]
    ordinals = ["1点目", "2点目", "3点目"]
    for ordinal, q in zip(ordinals, questions):
        lines.append(f"{ordinal}は、{q['title']}です。")
        for i, point in enumerate(q["points"], 1):
            lines.append(f"（{i}）{point.removesuffix('を伺う')}について伺います。")
        lines.append("")
    lines.append("以上、明確な答弁を求めまして、私の一般質問といたします。")

    return {
        "council": council,
        "respondents": respondents,
        "questions": questions,
        "script": "\n".join(lines),
        "cover_note": cover_note,
        "is_mock": True,
    }
