"""Social Posting — 疑似市民の声のX.com / Blueskyシェア機能

ニュース怒り再現エージェント（news_anger_agent.py）が生成した「疑似市民の声」
（pseudo_citizen_voice）を、ユーザー自身のSNSアカウントから投稿できるようにする。

方針:
  - 専用botアカウントは作らない。投稿は必ずユーザー自身のOAuth/app password認証済み
    アカウント経由で行う（本モジュールはトークンを受け取って都度クライアントを作るのみで、
    トークンの永続保存は行わない）。
  - 投稿テキストには必ず「これはAIが生成した疑似的な市民の反応例であり、実在の個人の
    発言ではない」旨の免責を自然な形で含める。個人情報は含めない。
  - ユーザーが認証情報を持たない場合は、投稿を行わずシェア用テキストのみを生成して返す
    （呼び出し側=app.pyがフォールバックとして提示する）。

弁護士法72条遵守: 本モジュールは投稿の代行（情報提供・書式作成支援）に徹し、
法的助言は行わない。最終的に投稿するか否かの判断は常にユーザー自身が行う。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

DISCLAIMER_PREFIX_BLUESKY = "※これはAIが生成した疑似的な市民の反応例です。実在の個人の発言ではありません。"
DISCLAIMER_SUFFIX_X = "\n\n※AIが生成した疑似的な市民の反応例（実在の個人の発言ではありません）"

X_MAX_CHARS = 280

try:
    from atproto import Client as _BlueskyClient
    ATPROTO_AVAILABLE = True
except ImportError:
    ATPROTO_AVAILABLE = False

try:
    import tweepy as _tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False


class SocialPostingError(Exception):
    """SNS投稿処理に関するエラー"""


@dataclass
class PostResult:
    success: bool
    post_url: Optional[str]
    posted_text: str
    platform: str


def _format_key_points(key_points: list[str], max_items: int = 2) -> str:
    return " / ".join(key_points[:max_items])


def build_share_text_bluesky(record: Dict[str, Any]) -> str:
    """Bluesky向けのシェアテキストを生成する（プレーンテキスト、改行可、文字数制限に比較的余裕あり）"""
    source = record.get("source_news", {})
    title = source.get("title", "")
    voice = record.get("pseudo_citizen_voice", "")
    key_points = record.get("key_points", [])

    lines = [
        DISCLAIMER_PREFIX_BLUESKY,
        "",
        f"【AI分析】{title}",
        "",
        voice,
    ]
    if key_points:
        lines += ["", "論点: " + _format_key_points(key_points, max_items=3)]
    if source.get("link"):
        lines += ["", source["link"]]
    lines += ["", "#CivicLens #情報公開請求"]
    return "\n".join(lines)


def build_share_text_x(record: Dict[str, Any]) -> Dict[str, Any]:
    """X向けのシェアテキストを生成する（280文字制限を考慮し、必要ならスレッド案も返す）"""
    source = record.get("source_news", {})
    title = source.get("title", "")
    voice = record.get("pseudo_citizen_voice", "")
    link = source.get("link", "")

    header = f"【AI分析】{title}に対する疑似市民の反応例\n"
    body = f"{header}{voice}"
    single_post = (body + DISCLAIMER_SUFFIX_X)
    if link:
        single_post_with_link = single_post + f"\n{link}"
    else:
        single_post_with_link = single_post

    if len(single_post_with_link) <= X_MAX_CHARS:
        return {"mode": "single", "posts": [single_post_with_link]}

    # 280文字を超える場合はスレッド分割案を提示する
    disclaimer_short = "※AIが生成した疑似市民の反応例（実在の発言ではありません）"
    thread: list[str] = []
    remaining = voice
    first_post = header.strip()
    thread.append(first_post[:X_MAX_CHARS])

    chunk_size = X_MAX_CHARS - 10  # 末尾に "(続く)" 等の余白を確保
    while remaining:
        chunk = remaining[:chunk_size]
        remaining = remaining[chunk_size:]
        suffix = " (続く)" if remaining else ""
        thread.append(chunk + suffix)

    last_post = disclaimer_short + (f"\n{link}" if link else "")
    thread.append(last_post[:X_MAX_CHARS])

    return {"mode": "thread", "posts": thread}


def build_share_texts(record: Dict[str, Any]) -> Dict[str, Any]:
    """プラットフォーム別のシェア用テキストをまとめて生成する（フォールバック提示用）"""
    return {
        "bluesky": build_share_text_bluesky(record),
        "x": build_share_text_x(record),
    }


def post_to_bluesky(text: str, handle: str, app_password: str) -> PostResult:
    """ユーザー自身のBlueskyアカウント（AT Protocol app password認証）で投稿する。

    handle: 例) "user.bsky.social"
    app_password: Blueskyの「アプリパスワード」（アカウント本パスワードではない）。
                  https://bsky.app/settings/app-passwords で発行する。
                  サーバー側では保存せず、リクエストの都度受け取って使い捨てる。
    """
    if not ATPROTO_AVAILABLE:
        raise SocialPostingError(
            "atproto パッケージがインストールされていません。`pip install atproto` を実行してください。"
        )
    if not handle or not app_password:
        raise SocialPostingError("Blueskyのhandleとapp passwordが必要です。")

    try:
        client = _BlueskyClient()
        client.login(handle, app_password)
        post = client.send_post(text=text)
    except Exception as e:
        raise SocialPostingError(f"Bluesky投稿に失敗しました: {e}") from e

    # atproto の send_post は AT URI (at://did/collection/rkey) を返す。
    # 閲覧用URLは https://bsky.app/profile/{did}/post/{rkey} の形式。
    post_url = None
    try:
        uri_parts = post.uri.split("/")
        rkey = uri_parts[-1]
        did = post.uri.split("at://")[1].split("/")[0]
        post_url = f"https://bsky.app/profile/{did}/post/{rkey}"
    except Exception:
        pass

    return PostResult(success=True, post_url=post_url, posted_text=text, platform="bluesky")


def post_to_x(
    text: str,
    access_token: str,
    access_token_secret: Optional[str] = None,
) -> PostResult:
    """ユーザー自身のXアカウントで投稿する。

    TODO: X API v2 の投稿には X Developer Portal でのアプリ登録（OAuth 1.0a の
    consumer key/secret、またはOAuth 2.0 User Contextのクライアント登録）が必要。
    無料枠（Free tier）は投稿数・機能に制限があるため、本番運用前に
    https://developer.x.com/en/portal/product でのプラン確認が必要。

    現状はプレースホルダー実装。X_CONSUMER_KEY / X_CONSUMER_SECRET を環境変数に
    設定し、tweepy が利用可能な場合のみ OAuth 1.0a User Context で投稿を試みる。
    """
    if not TWEEPY_AVAILABLE:
        raise SocialPostingError(
            "tweepy パッケージがインストールされていません。`pip install tweepy` を実行してください。"
        )

    import os

    consumer_key = os.getenv("X_CONSUMER_KEY")
    consumer_secret = os.getenv("X_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise SocialPostingError(
            "X_CONSUMER_KEY / X_CONSUMER_SECRET が未設定です。"
            "X Developer Portalでアプリを作成し、.envに設定してください（詳細は README 参照）。"
        )
    if not access_token or not access_token_secret:
        raise SocialPostingError("Xのaccess_tokenとaccess_token_secretが必要です（OAuth 1.0a User Context）。")

    try:
        auth_client = _tweepy.Client(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )
        response = auth_client.create_tweet(text=text[:X_MAX_CHARS])
        tweet_id = response.data.get("id") if response and response.data else None
        post_url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None
    except Exception as e:
        raise SocialPostingError(f"X投稿に失敗しました: {e}") from e

    return PostResult(success=True, post_url=post_url, posted_text=text[:X_MAX_CHARS], platform="x")
