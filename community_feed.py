"""Civic Lens — 「みんなの請求」統合フィード

これまで別々の仕組みだった2つの「みんなの請求」を1つのフィードに統合する:

  - DB版（visibility.py）: Civic Lens のFirestoreにPublic共有された開示請求。
    スター・フォークなど、Civic Lens 独自のコミュニティ機能を持つ。
  - オンチェーン版（onchain_ledger.py / ledger_tips.py）: EASに実際に刻印された
    開示請求と、市民が自分のウォレットから直接送った投げ銭（応援）。改変不能な
    「事実」として扱える。

このモジュールは両者を共通の表示用スキーマに正規化し、日付順 or 人気順で
1本のフィードとして返す。片方の取得元が失敗（未設定・チェーン障害等）しても
もう片方は表示できるよう、それぞれ独立に例外を握りつぶす。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from onchain_ledger import _find_authority, fetch_ledger_entries
from ledger_tips import all_time_tip_totals
from visibility import get_public_records, get_public_stats
from fork_star import get_record_stats


def _db_item(r) -> Dict[str, Any]:
    summary = r.summary_public or (r.user_input[:100] + ("..." if len(r.user_input) > 100 else ""))
    try:
        stats = get_record_stats(r.id)
        engagement_count = stats["stars"] + stats["forks"]
    except Exception:
        engagement_count = 0
    return {
        "source": "db",
        "id": r.id,
        "created_at": r.created_at,
        "author_label": r.anonymous_user_id or "匿名市民",
        "target_authority": r.target_authority_name,
        "category": r.category,
        "summary": summary,
        "status": r.status,
        "result_excerpt": r.result_excerpt,
        "is_public": True,
        "engagement_label": "スター",
        "engagement_count": engagement_count,
        "record_id": r.id,
        "submitted_date": r.submitted_date,
        "explorer_url": None,
        "tx_url": None,
        "signer_label": None,
    }


def _onchain_item(e: Dict[str, Any], tip_totals: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    info = _find_authority(e["authority"])
    tip_stats = tip_totals.get(e["uid"].lower(), {"tip_count": 0, "breakdown": {}})
    summary = e["requested_documents"] if e["is_public"] else "（内容は非公開・ハッシュのみオンチェーン記録）"
    return {
        "source": "onchain",
        "id": e["uid"],
        "created_at": e["recorded_at"],
        "author_label": e["signer_label"],
        "target_authority": e["authority"],
        "category": info.category if info else "自治体",
        "summary": summary,
        "status": (e.get("deadline") or {}).get("phase") or "記録済み",
        "result_excerpt": None,
        "is_public": e["is_public"],
        "engagement_label": "投げ銭",
        "engagement_count": tip_stats["tip_count"],
        "record_id": e.get("record_id") or e["uid"],
        "submitted_date": e["requested_date"] if e.get("requested_date_declared") else None,
        "explorer_url": e["explorer_url"],
        "tx_url": e["tx_url"],
        "signer_label": e["signer_label"],
    }


def fetch_unified_feed(
    category: Optional[str] = None,
    authority: Optional[str] = None,
    sort: str = "new",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """DB版Public共有 + オンチェーン台帳を1本のフィードにまとめて返す。

    sort="new"      -> 新着順（created_at desc）
    sort="ranking"   -> 人気順（DB: スター+フォーク数、オンチェーン: 全期間の投げ銭件数）
    """
    items: List[Dict[str, Any]] = []

    try:
        db_records = get_public_records(category=category, authority=authority, limit=limit)
        items.extend(_db_item(r) for r in db_records)
    except Exception:
        pass  # DB側が使えなくてもオンチェーン側は表示する

    try:
        entries = fetch_ledger_entries()
        try:
            tip_totals = all_time_tip_totals()
        except Exception:
            tip_totals = {}  # 投げ銭スキーマ未設定・障害でも台帳一覧は表示する
        onchain_items = [_onchain_item(e, tip_totals) for e in entries]
        if category:
            onchain_items = [it for it in onchain_items if it["category"] == category]
        if authority:
            onchain_items = [it for it in onchain_items if it["target_authority"] == authority]
        items.extend(onchain_items)
    except Exception:
        pass  # オンチェーン側が未設定・障害でもDB側は表示する

    if sort == "ranking":
        items.sort(key=lambda it: (it["engagement_count"] or 0), reverse=True)
    else:
        items.sort(key=lambda it: it["created_at"], reverse=True)

    return items[:limit]


def fetch_unified_stats() -> Dict[str, Any]:
    """統合フィードの統計サマリ（DB分 + オンチェーン分の合算）。"""
    stats = {
        "total_records": 0,
        "public_count": 0,
        "onchain_count": 0,
        "by_authority": {},
        "by_category": {},
    }

    try:
        db_stats = get_public_stats()
        stats["total_records"] += db_stats["total_records"]
        stats["public_count"] += db_stats["public_count"]
        for k, v in db_stats["by_authority"].items():
            stats["by_authority"][k] = stats["by_authority"].get(k, 0) + v
        for k, v in db_stats["by_category"].items():
            stats["by_category"][k] = stats["by_category"].get(k, 0) + v
    except Exception:
        pass

    try:
        entries = fetch_ledger_entries()
        stats["total_records"] += len(entries)
        stats["public_count"] += sum(1 for e in entries if e["is_public"])
        stats["onchain_count"] = len(entries)
        for e in entries:
            info = _find_authority(e["authority"])
            cat = info.category if info else "自治体"
            stats["by_authority"][e["authority"]] = stats["by_authority"].get(e["authority"], 0) + 1
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
    except Exception:
        pass

    return stats
