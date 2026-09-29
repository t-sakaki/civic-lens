"""News Collector Agent

「怒りの再現→開示請求」パイプラインの入口を担当するエージェント。
ユーザーが指定した地域（市区町村名など）に関するニュースを、Google Newsの検索RSSから
自動収集する。ニュース本文の全文取得はサイトごとに構造が異なり不安定なため、
デモではRSSのタイトル・スニペット・リンクを1件分の「記事」として扱う。

将来的にはユーザーのGPS/行動履歴から地域を推定するモジュール（別途）と組み合わせ、
region引数を自動決定する想定。
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"

# 自治体・警察・公的行事の話題に絞り込むためのデフォルトキーワード
DEFAULT_TOPIC_KEYWORDS = ["市", "県", "町", "村", "警察", "自治体", "大会", "式典"]


@dataclass
class NewsItem:
    title: str
    link: str
    published: Optional[str]
    summary: str

    def as_text(self) -> str:
        """怒り再現エージェントに渡すためのプレーンテキスト化"""
        parts = [self.title]
        if self.summary and self.summary != self.title:
            parts.append(self.summary)
        return "\n".join(parts)


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class NewsCollectorAgent:
    """地域名からGoogle News RSSを検索し、関連ニュースを取得するエージェント"""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def fetch_news(
        self,
        region: str,
        extra_keywords: Optional[List[str]] = None,
        max_items: int = 5,
    ) -> List[NewsItem]:
        """指定地域に関するニュースを検索して取得する。

        region: 例) "名古屋市", "愛知県"
        extra_keywords: 例) ["警察", "アジア大会"] のように話題を絞りたい場合
        """
        keywords = [region] + (extra_keywords or [])
        query = " ".join(keywords)
        url = f"{GOOGLE_NEWS_RSS_URL}?q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"

        try:
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"[NewsCollectorAgent] ニュース取得に失敗: {e}")
            return []

        items: List[NewsItem] = []
        for item in root.findall(".//item")[:max_items]:
            title = _strip_html(item.findtext("title") or "")
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate")
            description = _strip_html(item.findtext("description") or "")
            if not title:
                continue
            items.append(NewsItem(title=title, link=link, published=pub_date, summary=description))
        return items

    def fetch_top_news(self, region: str, extra_keywords: Optional[List[str]] = None) -> Optional[NewsItem]:
        """最新1件だけを取得する（デモの単純フローで利用）"""
        items = self.fetch_news(region, extra_keywords=extra_keywords, max_items=1)
        return items[0] if items else None


def get_news_collector_agent() -> NewsCollectorAgent:
    return NewsCollectorAgent()
