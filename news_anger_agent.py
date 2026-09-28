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
from timeout_utils import call_with_timeout


PSEUDO_VOICE_DISCLAIMER = (
    "この「市民の声」は実在の個人の発言ではなく、ニュース記事の論点をもとにAIが生成したフィクションです。"
    "実在の人物の発言として引用・転載しないでください。"
)


# テーマ別の怒り再現エージェント設定。
# 同じニュース記事でも、どの立場（テーマ）から読むかで論点・声が変わる想定。
# 単一クラスの乱立を避けるため、AngerReproductionAgent は theme をパラメータとして受け取り、
# プロンプトのペルソナ部分だけを差し替える設計にしている（news_collector_agent.py の
# THEME_SEARCH_HINTS と対で使う）。
NEWS_THEMES: dict[str, dict[str, str]] = {
    "general": {
        "label": "一般（行政監視）",
        "persona": "税金の使途・意思決定過程の不透明さ・住民負担など、行政監視全般の視点",
    },
    "gender": {
        "label": "ジェンダー",
        "persona": (
            "女性・性的マイノリティを含むジェンダーの視点。審議会・意思決定層の男女比の偏り、"
            "性別による負担や扱いの格差、ハラスメント対応の不透明さなど"
        ),
    },
    "poverty": {
        "label": "貧困・生活困窮",
        "persona": (
            "生活困窮者・低所得世帯の視点。福祉予算の使途、支援制度の周知不足や利用しにくさ、"
            "困窮世帯への負担のしわ寄せなど"
        ),
    },
    "fairness": {
        "label": "公正・利益相反",
        "persona": (
            "行政運営の公正性の視点。特定業者・団体への利益誘導、入札や随意契約の妥当性、"
            "意思決定プロセスの公平性など"
        ),
    },
    "climate": {
        "label": "気候変動",
        "persona": (
            "気候変動対策の視点。公共事業やイベントのCO2排出・環境負荷、再エネ関連予算の使途、"
            "脱炭素目標との整合性など"
        ),
    },
    "biodiversity": {
        "label": "生物多様性",
        "persona": (
            "生物多様性・自然環境保護の視点。開発事業による生態系への影響、緑地・里山の減少、"
            "環境アセスメントの妥当性など"
        ),
    },
    "human_rights": {
        "label": "人権（国際人権）",
        "persona": (
            "国際人権基準に照らした視点。外国人・障害者・子どもなど弱い立場の人々への配慮の欠如、"
            "適正手続きの保障、差別的な取り扱いの可能性など"
        ),
    },
}

DEFAULT_THEME = "general"


def get_theme(theme: str | None) -> dict[str, str]:
    return NEWS_THEMES.get(theme or DEFAULT_THEME, NEWS_THEMES[DEFAULT_THEME])


NEWS_ANALYSIS_PROMPT = """あなたは行政監視の視点を持つジャーナリストAIです。
以下のニュース記事を読み、一見すると市民が喜んでいる/好意的に報じられているように見える場合でも、
批判的に見た場合に疑問視されうる論点を洗い出してください。

特に、次の観点を重視してください: {theme_persona}
{region_line}
【ニュース記事】
{news_text}

以下のJSON形式のみで出力してください（前後に説明文やコードブロック記号は不要）:
{{
  "key_points": ["論点1", "論点2", "論点3"],
  "pseudo_citizen_voice": "この論点をもとに、実際にそのニュースの対象地域に住む市民が怒っているかのような一人称の文章（SNS投稿や生活実感のこもった口調で200文字程度）"
}}
"""


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
                response = call_with_timeout(
                    self._civic_agent.genai_client.models.generate_content,
                    model="gemini-pro-latest",
                    contents=NEWS_ANALYSIS_PROMPT.format(
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
    from news_collector_agent import theme_search_keywords

    collector = get_news_collector_agent()
    extra_keywords = ([keyword] if keyword else []) + theme_search_keywords(theme)
    news_item: NewsItem | None = collector.fetch_top_news(region, extra_keywords=extra_keywords or None)

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
    parser.add_argument("--news-file", help="ニュース本文を直接貼り付けたテキストファイル（従来モード）")
    args = parser.parse_args()

    if args.region:
        result = run_pipeline_from_region(args.region, keyword=args.keyword)
    elif args.news_file:
        with open(args.news_file, encoding="utf-8") as f:
            news_text = f.read()
        result = run_pipeline(news_text)
    else:
        news_text = sys.stdin.read()
        if not news_text.strip():
            print("使い方: --region <地域名> | --news-file <path> | 標準入力でニュース本文を渡す", file=sys.stderr)
            sys.exit(1)
        result = run_pipeline(news_text)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
