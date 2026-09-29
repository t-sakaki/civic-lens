"""Anger Reproduction Agent + Pipeline Orchestrator (demo)

役割分担:
  1. NewsCollectorAgent (news_collector_agent.py)
     地域名からニュースをネットから自動収集する。
  2. AngerReproductionAgent (このファイル)
     ニュース記事から、論点整理と擬似的な市民の怒りの声を生成する。
  3. DisclosureRequestAgent (agent.py の CivicLensAgent.analyze_anger)
     擬似的な怒りの声を、開示請求書の該当箇所（対象機関・請求文書・根拠条例など）に変換する。
     個人情報（氏名・住所等）の記入欄は含めない。

このファイルはパイプライン全体（ニュース収集 → 怒り再現 → 開示請求）を
オーケストレーションするCLIエントリポイントも兼ねる。

使い方:
    python news_anger_agent.py --region 名古屋市
    python news_anger_agent.py --region 名古屋市 --keyword アジア大会
    python news_anger_agent.py --news-file path/to/news.txt   # 貼り付け入力（従来モード）
"""
import argparse
import sys
import json

from agent import get_agent, GENAI_AVAILABLE
from google.genai import types  # type: ignore
from news_collector_agent import get_news_collector_agent, NewsItem
from gemini_models import generate as gemini_generate


PSEUDO_VOICE_DISCLAIMER = (
    "この「市民の声」は実在の個人の発言ではなく、ニュース記事の論点をもとにAIが生成したフィクションです。"
    "実在の人物の発言として引用・転載しないでください。"
)


# SDGs 17目標それぞれの怒り再現エージェント設定。
# ニュースはテーマに関係なく地域だけで収集し、集めた同じ記事を、各エージェントが自分の
# SDG目標の観点から分析する（ニュースをテーマで検索し直すのではない）。
# 単一クラスの乱立を避けるため、AngerReproductionAgent は theme をパラメータとして受け取り、
# プロンプトのペルソナ部分だけを差し替える設計にしている。
# "general" は特定のSDG目標に寄らない行政監視全般の視点（既定・過去の記録との互換用）。
_SDG_AGENTS: list[tuple[str, str, str]] = [
    ("目標1 貧困をなくそう", "貧困・生活困窮の視点",
     "生活困窮者・低所得世帯への影響、福祉予算の使途、支援制度の周知不足や使いにくさ、負担のしわ寄せなど"),
    ("目標2 飢餓をゼロに", "食料・栄養の視点",
     "子ども食堂・給食・フードバンクなど食へのアクセス、食料の安定供給、農業・地産地消施策の実効性、食品ロスなど"),
    ("目標3 すべての人に健康と福祉を", "健康・医療・福祉の視点",
     "医療・介護・保健サービスへのアクセス、地域医療体制、メンタルヘルス、感染症対策、事故・安全対策の妥当性など"),
    ("目標4 質の高い教育をみんなに", "教育の視点",
     "学校・保育・社会教育の質と機会の格差、教育予算の使途、子どもの学習環境、不登校・特別支援への対応など"),
    ("目標5 ジェンダー平等を実現しよう", "ジェンダーの視点",
     "審議会・意思決定層の男女比の偏り、性別による負担や扱いの格差、性的マイノリティへの配慮、ハラスメント対応の不透明さなど"),
    ("目標6 安全な水とトイレを世界中に", "水・衛生の視点",
     "上下水道・水質・治水の整備と料金負担、水源保全、災害時の水・トイレの確保、老朽インフラの更新計画など"),
    ("目標7 エネルギーをみんなに そしてクリーンに", "エネルギーの視点",
     "再生可能エネルギー施策、公共施設の省エネ、電気料金・エネルギー価格高騰への支援、エネルギー事業者選定の妥当性など"),
    ("目標8 働きがいも経済成長も", "労働・地域経済の視点",
     "公共調達や委託での労働条件・賃金、会計年度任用職員など非正規の扱い、地域産業・雇用施策の費用対効果など"),
    ("目標9 産業と技術革新の基盤をつくろう", "インフラ・産業・技術革新の視点",
     "公共事業・インフラ整備の必要性と費用対効果、IT・DX投資の妥当性、ベンダー依存、老朽化対策など"),
    ("目標10 人や国の不平等をなくそう", "格差・不平等の視点",
     "地域間・世代間・所得間の格差、障害者・外国人・高齢者など特定の人々が取り残されていないか、施策の恩恵の偏りなど"),
    ("目標11 住み続けられるまちづくりを", "まちづくり・防災の視点",
     "都市計画・再開発、住宅・交通・公共空間、防災・減災、騒音・交通規制など住民生活への負担、住民参加の不足など"),
    ("目標12 つくる責任 つかう責任", "資源循環・消費の視点",
     "廃棄物処理・リサイクル、ごみ処理施設の費用、公共調達（グリーン購入）、イベント等の使い捨て・過剰消費、食品ロスなど"),
    ("目標13 気候変動に具体的な対策を", "気候変動の視点",
     "公共事業やイベントのCO2排出・環境負荷、脱炭素目標との整合性、気候変動対策予算の使途、気候災害への備えなど"),
    ("目標14 海の豊かさを守ろう", "海洋・水辺の視点",
     "海洋汚染・プラスチックごみ、沿岸・河川・水辺の開発、漁業資源、下水・排水処理の影響など"),
    ("目標15 陸の豊かさも守ろう", "生物多様性・自然環境の視点",
     "開発事業による生態系への影響、緑地・里山・森林の減少、環境アセスメントの妥当性、鳥獣害対策など"),
    ("目標16 平和と公正をすべての人に", "公正・透明性・司法の視点",
     "情報公開の不備、意思決定過程の不透明さ、特定業者・団体への利益誘導、入札・随意契約の妥当性、警察・司法の適正手続きなど"),
    ("目標17 パートナーシップで目標を達成しよう", "連携・官民協働の視点",
     "民間・NPO・住民との協働の実態、委託・協定の透明性、国際・自治体間連携の成果検証、住民参加の実質性など"),
]

NEWS_THEMES: dict[str, dict[str, str]] = {
    "general": {
        "label": "一般（行政監視）",
        "persona": "税金の使途・意思決定過程の不透明さ・住民負担など、行政監視全般の視点",
    },
}
for _i, (_goal, _viewpoint, _focus) in enumerate(_SDG_AGENTS, start=1):
    NEWS_THEMES[f"sdg{_i}"] = {
        "label": _goal,
        "persona": f"SDGs「{_goal}」の担当として、{_viewpoint}。{_focus}",
    }

DEFAULT_THEME = "general"


def get_theme(theme: str | None) -> dict[str, str]:
    return NEWS_THEMES.get(theme or DEFAULT_THEME, NEWS_THEMES[DEFAULT_THEME])


NEWS_ANALYSIS_PROMPT = """あなたは行政監視の視点を持つジャーナリストAIです。
以下のニュース記事を読み、一見すると市民が喜んでいる/好意的に報じられているように見える場合でも、
批判的に見た場合に疑問視されうる論点を洗い出してください。

あなたの担当（この観点だけからニュースを分析すること）: {theme_persona}
記事がこの観点と直接関係しない場合でも、記事の内容から読み取れる範囲で、この観点から見て見過ごされている
影響・負担・不透明な点を指摘してください（記事にない事実を断定せず、「確認されていない」「説明がない」という形で）。
{region_line}
【ニュース記事】
{news_text}

以下のJSON形式のみで出力してください（前後に説明文やコードブロック記号は不要）:
{{
  "key_points": ["論点1", "論点2", "論点3"],
  "pseudo_citizen_voice": "この論点をもとに、実際にそのニュースの対象地域に住む市民が怒っているかのような一人称の文章（SNS投稿や生活実感のこもった口調で200文字程度）"
}}
"""


# 各SDGsエージェントが「自分の出番だ」と判断するためのキーワード（Gemini未設定時の代替判定用）
_SDG_KEYWORDS: dict[str, list[str]] = {
    "sdg1": ["貧困", "生活困窮", "生活保護", "低所得", "物価高", "給付金"],
    "sdg2": ["食料", "給食", "子ども食堂", "農業", "食品ロス", "米価"],
    "sdg3": ["医療", "病院", "介護", "健康", "感染", "救急", "メンタル", "事故"],
    "sdg4": ["学校", "教育", "保育", "児童", "生徒", "不登校", "教員"],
    "sdg5": ["女性", "男女", "ジェンダー", "審議会", "ハラスメント", "LGBT"],
    "sdg6": ["水道", "下水", "水質", "治水", "浸水", "トイレ"],
    "sdg7": ["電気", "エネルギー", "再エネ", "太陽光", "省エネ", "電力"],
    "sdg8": ["雇用", "賃金", "労働", "非正規", "会計年度", "産業", "観光"],
    "sdg9": ["インフラ", "橋", "道路", "DX", "システム", "老朽", "公共事業", "建設"],
    "sdg10": ["格差", "障害", "外国人", "高齢者", "差別", "多文化"],
    "sdg11": ["まちづくり", "再開発", "防災", "避難", "交通規制", "住宅", "駅前", "大会", "イベント", "式典"],
    "sdg12": ["ごみ", "廃棄物", "リサイクル", "使い捨て", "焼却", "調達"],
    "sdg13": ["気候", "脱炭素", "CO2", "猛暑", "温暖化", "豪雨"],
    "sdg14": ["海", "漁業", "海洋", "河川", "プラスチック", "港"],
    "sdg15": ["森林", "緑地", "里山", "開発", "生態系", "環境アセス", "クマ", "鳥獣"],
    "sdg16": ["情報公開", "入札", "随意契約", "不祥事", "警察", "逮捕", "議会", "不透明", "裏金", "予算"],
    "sdg17": ["連携", "協定", "委託", "民間", "NPO", "協働", "官民", "提携"],
}

MAX_APPEARING_AGENTS = 3

APPEARANCE_PROMPT = """あなたはSDGs17目標それぞれの担当エージェントたちの進行役です。
以下のニュース記事を読み、各エージェントのうち「この記事は自分の担当の観点から見過ごせない」と
自律的に名乗り出るべきエージェントだけを選び、最大{max_agents}体まで、それぞれの一言コメントを書いてください。
関係が薄いエージェントは登場させないこと。担当外の目標を無理にこじつけないこと。
記事にない事実は断定せず、「説明がない」「確認されていない」という形で疑問を述べてください。

【エージェント一覧】
{agent_list}
{region_line}
【ニュース記事】
{news_text}

以下のJSON形式のみで出力してください（前後に説明文やコードブロック記号は不要）:
{{"appearances": [{{"theme": "sdg5", "remark": "そのエージェントの観点からの一言（40文字程度）", "anger_level": 1〜10の整数}}]}}
"""


def _fallback_appearances(news_text: str) -> list[dict]:
    scored = []
    for theme, words in _SDG_KEYWORDS.items():
        hits = [w for w in words if w in news_text]
        if hits:
            scored.append((len(hits), theme, hits[0]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        scored = [(0, "sdg16", "")]
    result = []
    for n, theme, word in scored[:MAX_APPEARING_AGENTS]:
        label = NEWS_THEMES[theme]["label"]
        remark = (
            f"「{word}」に関わる話だが、{label.split(' ', 1)[-1]}の観点で影響や費用の説明が十分か確認したい。"
            if word
            else "税金の使途や意思決定の経緯が記事から見えない。説明を求めたい。"
        )
        result.append({"theme": theme, "remark": remark, "anger_level": min(4 + n, 8)})
    return result


def select_appearing_agents(news_text: str, region: str | None = None) -> list[dict]:
    """ニュース記事に対して、どのSDGsエージェントが自律的に「登場」するかを判定する。

    ユーザーの操作を待たず、エージェント側が記事を読んで自分の出番かを判断する。
    17体ぶんを1回のGemini呼び出しでまとめて判定し、コストを抑える。
    戻り値: [{"theme", "label", "remark", "anger_level"}]（怒りレベルの高い順、最大MAX_APPEARING_AGENTS件）
    """
    civic_agent = get_agent()
    raw: list[dict] | None = None
    if GENAI_AVAILABLE and civic_agent.genai_client:
        agent_list = "\n".join(
            f"- {k}: {v['label']}（{v['persona']}）" for k, v in NEWS_THEMES.items() if k != "general"
        )
        region_line = f"\nこのニュースは「{region}」について検索して見つけた記事です。\n" if region else ""
        try:
            # 登場判定は軽い分類タスクなので、速度とコストを優先して軽量モデルを使う
            response = gemini_generate(
                civic_agent.genai_client,
                "flash",
                APPEARANCE_PROMPT.format(
                    max_agents=MAX_APPEARING_AGENTS,
                    agent_list=agent_list,
                    region_line=region_line,
                    news_text=news_text,
                ),
                config=types.GenerateContentConfig(response_mime_type="application/json"),
                timeout_s=20.0,
            )
            text = response.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            raw = json.loads(text.strip()).get("appearances")
        except Exception as e:
            print(f"[select_appearing_agents] Gemini呼び出しに失敗、キーワード判定を使用します: {e}", file=sys.stderr)

    if raw is None:
        raw = _fallback_appearances(news_text)

    seen: set[str] = set()
    result: list[dict] = []
    for a in raw if isinstance(raw, list) else []:
        theme = a.get("theme") if isinstance(a, dict) else None
        if theme not in NEWS_THEMES or theme == "general" or theme in seen or not a.get("remark"):
            continue
        seen.add(theme)
        try:
            level = max(1, min(10, int(a.get("anger_level", 5))))
        except (TypeError, ValueError):
            level = 5
        result.append({"theme": theme, "label": NEWS_THEMES[theme]["label"], "remark": str(a["remark"]), "anger_level": level})
    result.sort(key=lambda x: -x["anger_level"])
    return result[:MAX_APPEARING_AGENTS]


class AngerReproductionAgent:
    """ニュース記事本文から、論点整理と擬似的な市民の怒りの声を生成するエージェント"""

    def __init__(self):
        self._civic_agent = get_agent()

    def generate(self, news_text: str, region: str | None = None, theme: str | None = None) -> dict:
        region_line = ""
        if region:
            region_line = (
                f"\nこのニュースはユーザーが「{region}」について検索して見つけた記事です。"
                f"記事本文が具体的な自治体名に触れていない場合でも、論点整理と擬似的な市民の声は"
                f"「{region}」の住民・行政を念頭に置いて生成してください（他の地域や県全体の話にすり替えないこと）。\n"
            )
        theme_persona = get_theme(theme)["persona"]
        if GENAI_AVAILABLE and self._civic_agent.genai_client:
            try:
                response = gemini_generate(
                    self._civic_agent.genai_client,
                    "pro",
                    NEWS_ANALYSIS_PROMPT.format(
                        news_text=news_text, region_line=region_line, theme_persona=theme_persona
                    ),
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                    timeout_s=25.0,
                )
                text = response.text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                data = json.loads(text.strip())
                if data.get("key_points") and data.get("pseudo_citizen_voice"):
                    return data
            except Exception as e:
                print(f"[AngerReproductionAgent] Gemini呼び出しに失敗、フォールバックを使用します: {e}", file=sys.stderr)

        # フォールバック（API未設定/失敗時のデモ用）
        return {
            "key_points": [
                "華やかな式典・大会運営の裏で、税金や公金の具体的な使途が記事中で明示されていない",
                "警備体制や動員規模が過剰でないか、その費用対効果が検証されていない",
                "地域住民への負担（交通規制・騒音・生活道路の制限等）が記事では触れられていない",
            ],
            "pseudo_citizen_voice": (
                "ニュースだと成功したイベントみたいに報じられてるけど、実際近くに住んでる身としては"
                "交通規制もすごかったし、警備にどれだけ税金使ったのか全然説明がない。"
                "喜んでる人だけがニュースになってるだけで、こっちは正直不満しかない。"
                "ちゃんと使ったお金の内訳を見せてほしい。"
            ),
        }


def run_pipeline(
    news_text: str,
    source: dict | None = None,
    region: str | None = None,
    theme: str | None = None,
) -> dict:
    """怒り再現 → 開示請求の該当箇所生成までを実行する

    region: ユーザーが検索した対象地域。記事本文だけでは対象機関が曖昧な場合に、
    無関係な自治体へ誤って紐づかないよう、対象機関特定のヒントとして使う。
    theme: どの立場から怒るか（NEWS_THEMES のキー。未指定なら DEFAULT_THEME）。
    """
    anger_agent = AngerReproductionAgent()
    step1 = anger_agent.generate(news_text, region=region, theme=theme)
    pseudo_voice = step1["pseudo_citizen_voice"]

    from ordinance_data import match_authority_by_text

    hint_authority_key = match_authority_by_text(region, default="") if region else ""
    disclosure_agent = get_agent()
    analysis = disclosure_agent.analyze_anger(pseudo_voice, hint_authority_key=hint_authority_key or None)

    disclosure_excerpt = {
        "対象機関": analysis.target_authority,
        "請求する行政文書": analysis.specific_documents_requested,
        "根拠条例": analysis.legal_basis,
        "請求理由の要約": analysis.pain_summary,
        "推奨対応期限": analysis.recommended_response_time,
    }

    result = {
        "theme": theme or DEFAULT_THEME,
        "key_points": step1["key_points"],
        "pseudo_citizen_voice": pseudo_voice,
        "pseudo_citizen_voice_disclaimer": PSEUDO_VOICE_DISCLAIMER,
        "disclosure_request_excerpt": disclosure_excerpt,
        "anger_level": analysis.anger_level,
        "is_mock": analysis.is_mock,
    }
    if source:
        result["source_news"] = source
    return result


def run_pipeline_from_region(region: str, keyword: str | None = None, theme: str | None = None) -> dict:
    """地域名からニュースを自動収集し、パイプラインを実行する"""
    collector = get_news_collector_agent()
    news_item: NewsItem | None = collector.fetch_top_news(region, extra_keywords=[keyword] if keyword else None)

    if news_item is None:
        raise RuntimeError(
            f"'{region}' に関するニュースが取得できませんでした（ネットワーク未接続、または該当ニュースなし）。"
        )

    source = {"title": news_item.title, "link": news_item.link, "published": news_item.published}
    return run_pipeline(news_item.as_text(), source=source, region=region, theme=theme)


def main():
    parser = argparse.ArgumentParser(description="News → Anger Reproduction → Disclosure Request (demo)")
    parser.add_argument("--region", help="ニュースを自動収集する対象地域（例: 名古屋市）")
    parser.add_argument("--keyword", help="地域名に加えて絞り込むキーワード（例: アジア大会）")
    parser.add_argument("--theme", default=None, help="怒りエージェントID（general / sdg1〜sdg17）")
    parser.add_argument("--news-file", help="ニュース本文を直接貼り付けたテキストファイル（従来モード）")
    args = parser.parse_args()

    if args.region:
        result = run_pipeline_from_region(args.region, keyword=args.keyword, theme=args.theme)
    elif args.news_file:
        with open(args.news_file, encoding="utf-8") as f:
            news_text = f.read()
        result = run_pipeline(news_text, theme=args.theme)
    else:
        news_text = sys.stdin.read()
        if not news_text.strip():
            print("使い方: --region <地域名> | --news-file <path> | 標準入力でニュース本文を渡す", file=sys.stderr)
            sys.exit(1)
        result = run_pipeline(news_text, theme=args.theme)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
