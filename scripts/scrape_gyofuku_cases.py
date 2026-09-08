"""
総務省「行政不服審査裁決・答申検索データベース」から、情報公開・個人情報保護
関連の法令に基づく「認容」（一部認容を含む）事例を収集し、data/gyofuku_cases.json
に蓄積するスクレイパー。

## 利用規約(PDL1.0)への配慮
- https://fufukudb.search.soumu.go.jp/koukai/PDL に掲載されている「公共データ利用規約
  （PDL1.0）」により、コンテンツ利用時は出典明記が必須であり、答申・裁決本文の著作権は
  各行政庁に留保されうる旨が明記されている。
- そのため本スクレイパーは、検索結果一覧に表示される「概要」欄（データベース自身が
  生成する短い抜粋・複数箇所が "…" で省略されたスニペット）のみを保存し、裁決・答申の
  全文は保存しない。各レコードには出典(出典URL・データベース名)を必ず添付する。
- サーバー負荷に配慮し、リクエスト（検索・ページ送り）の間に待機時間を入れる。
- 実行は手動・低頻度（本スクリプトを人間が明示的に実行した場合のみ）を想定している。

## 使い方
    .venv/bin/python scripts/scrape_gyofuku_cases.py [--max-pages-per-query 6]

Playwright (Chromium) が必要:
    pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "gyofuku_cases.json"
SITE_ROOT = "https://fufukudb.search.soumu.go.jp/koukai"

# 情報公開・個人情報保護・公文書管理に関連する法令のキーワード
TARGET_LAW_KEYWORDS = [
    "情報公開",
    "個人情報保護",
    "個人情報の保護",
    "公文書管理",
    "公文書等の管理",
]

# 検索クエリ（フリーワード）。
# 従来は「法令キーワード + 認容」（例:「情報公開 認容」）で検索していたが、
# フリーワード検索が両語を含む文書に絞り込む(AND的な)挙動のため取りこぼしが大きく、
# 実際には「認容」単独で検索した場合の総件数（裁決382件・答申471件、2026年時点）の
# 1割強しか収集できていなかった。そのため「認容」単独で検索し、法令の関連性は
# 取得後に _is_relevant_law() でローカルにフィルタする方式に変更した。
SEARCH_KEYWORDS = ["認容"]

WAIT_BETWEEN_REQUESTS_SEC = 2.0

# vc="J002"(裁決検索) / "J005"(答申検索) の設定差分
#
# 検索結果テーブルの列構成が2種類で異なる点に注意:
#   J002(裁決検索): No/審査庁名/種類/処分根拠法令/審査会等名 → 裁決日(文書番号)/裁決結果/裁決の概要/答申日
#     → 「裁決結果」列に "一部認容" 等の明確な結果ラベルが入っている。
#   J005(答申検索): No/審査会等名/種類/処分根拠法令/審査庁名 → 答申日(答申番号)/答申の概要/裁決日
#     → 結果ラベルの専用列が存在しない。「答申の概要」テキスト中の「認容」の語から結果を推定する。
#   （審査庁名と審査会等名の列順も入れ替わっている点にも注意）
SEARCH_TYPES = [
    {
        "vc": "J002",
        "category": "裁決",
        "sort_order_value": "05",  # 裁決日順
        "id_field": "saiketsuId",
        "date_label": "裁決日",
        "authority_col": 1,
        "council_col": 4,
        "result_col": 6,     # 裁決結果（明示的な列がある）
        "summary_col": 7,    # 裁決の概要
    },
    {
        "vc": "J005",
        "category": "答申",
        "sort_order_value": "04",  # 答申日順
        "id_field": "toshinId",
        "date_label": "答申日",
        "authority_col": 4,
        "council_col": 1,
        "result_col": None,  # 専用列なし。summary_colのテキストから推定する
        "summary_col": 6,    # 答申の概要
    },
]


@dataclass
class PrecedentCase:
    case_id: str  # 例: "J002-14711"
    category: str  # "裁決" or "答申"
    authority: str  # 審査庁名 / 諮問庁名
    council_name: str  # 行政不服審査会等の名称
    kind: str  # 不服申立ての種類
    basis_laws: str  # 処分根拠法令
    decision_date: str  # 裁決日 / 答申日
    document_number: str  # 裁決・文書番号
    result: str  # 裁決結果 / 答申結果（認容・一部認容・棄却・却下・その他）
    summary: str  # データベースの検索結果一覧に表示される概要スニペット（全文ではない）
    source_url: str  # 個別ページURL
    attribution: str  # 出典表記（PDL1.0で必須）
    matched_keyword: str  # どのキーワードにヒットしたか
    collected_at: str


def _is_relevant_law(basis_laws: str) -> bool:
    return any(k in basis_laws for k in TARGET_LAW_KEYWORDS)


def _is_granted(result_text: str) -> bool:
    """認容・一部認容のみを対象とする（棄却・却下・その他は除外）。"""
    return "認容" in result_text


def _infer_toshin_result(summary_text: str) -> str:
    """答申検索(J005)には結果専用の列がないため、答申の概要テキスト中の
    「認容」という語の直前十数文字に「一部」が含まれるかで一部認容/認容を推定する。
    ハイライト表示（<span class="highlight">）のタグ除去で語の前後に空白が入るため、
    厳密な隣接一致ではなく直前ウィンドウの部分一致で判定する。
    """
    if not summary_text:
        return ""
    idx = summary_text.find("認容")
    if idx == -1:
        return ""
    window = summary_text[max(0, idx - 15):idx]
    return "一部認容" if "一部" in window else "認容"


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def scrape(max_pages_per_query: int = 6, max_records: int = 1000, headless: bool = True) -> list[PrecedentCase]:
    from playwright.sync_api import sync_playwright

    collected: dict[str, PrecedentCase] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            for cfg in SEARCH_TYPES:
                for keyword in SEARCH_KEYWORDS:
                    if len(collected) >= max_records:
                        break
                    _scrape_one_query(browser, cfg, keyword, max_pages_per_query, collected, now_iso)
        finally:
            browser.close()

    return list(collected.values())


def _scrape_one_query(browser, cfg: dict, keyword: str, max_pages: int,
                       collected: dict, now_iso: str) -> None:
    page = browser.new_page()
    try:
        page.goto(f"{SITE_ROOT}/Main", timeout=30000)
        page.wait_for_load_state("networkidle")
        page.click(f'div.blocktitle.menu[name={cfg["vc"]}]')
        page.wait_for_load_state("networkidle")
        time.sleep(WAIT_BETWEEN_REQUESTS_SEC)

        page.fill("#freewordQuery", keyword)
        page.select_option("#sortOrder", cfg["sort_order_value"])
        page.click("#dispCount4")  # 100件表示
        page.click("button.large.search")
        page.wait_for_load_state("networkidle")
        time.sleep(WAIT_BETWEEN_REQUESTS_SEC)

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                page.fill("#currentPage", str(page_num))
                page.click("button.small.goPage")
                page.wait_for_load_state("networkidle")
                time.sleep(WAIT_BETWEEN_REQUESTS_SEC)

            html = page.content()
            rows = _parse_result_rows(html, cfg)
            if not rows:
                break
            for row in rows:
                if not _is_granted(row["result"]) or not _is_relevant_law(row["basis_laws"]):
                    continue
                case_id = f'{cfg["vc"]}-{row["case_ref_id"]}'
                if case_id in collected:
                    continue
                matched_law_keyword = next(
                    (k for k in TARGET_LAW_KEYWORDS if k in row["basis_laws"]), keyword
                )
                source_url = (
                    f'{SITE_ROOT}/Main?vc=&sc=select&{"J004" if cfg["vc"] == "J002" else "J007"}='
                    f'&{cfg["id_field"]}={row["case_ref_id"]}'
                )
                collected[case_id] = PrecedentCase(
                    case_id=case_id,
                    category=cfg["category"],
                    authority=row["authority"],
                    council_name=row["council_name"],
                    kind=row["kind"],
                    basis_laws=row["basis_laws"],
                    decision_date=row["decision_date"],
                    document_number=row["document_number"],
                    result=row["result"],
                    summary=row["summary"],
                    source_url=source_url,
                    attribution=f"出典：行政不服審査裁決・答申検索データベース（{source_url}）",
                    matched_keyword=matched_law_keyword,
                    collected_at=now_iso,
                )

            # 総件数から最終ページに達したら打ち切り
            m = re.search(r"現在<span>(\d+)</span>/<span>(\d+)</span>ページ目", html)
            if m and int(m.group(1)) >= int(m.group(2)):
                break
    except Exception as e:  # noqa: BLE001
        print(f"[warn] query={keyword!r} type={cfg['category']} 収集中にエラー: {e}")
    finally:
        page.close()


def _parse_result_rows(html: str, cfg: dict) -> list[dict]:
    """検索結果一覧の <tbody class="link"> ブロックを正規表現で抽出する。"""
    rows = []
    blocks = re.findall(r'<tbody class="link">(.*?)</tbody>', html, re.S)
    for block in blocks:
        id_m = re.search(r'class="' + cfg["id_field"] + r'"[^>]*>(\d+)', block)
        if not id_m:
            continue
        case_ref_id = id_m.group(1)

        cells = re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)

        def cell_text(idx: Optional[int]) -> str:
            if idx is None or idx >= len(cells):
                return ""
            raw = cells[idx]
            raw = re.sub(r"<[^>]+>", " ", raw)
            return _clean(raw)

        # J002/J005で列順（審査庁名・審査会等名）と結果列の有無が異なるため、
        # cfgのカラム設定に従って抽出する（SEARCH_TYPESのコメント参照）。
        authority = cell_text(cfg["authority_col"])
        kind = cell_text(2)
        basis_laws = cell_text(3)
        council_name = cell_text(cfg["council_col"])
        date_and_docnum = cell_text(5)
        summary = cell_text(cfg["summary_col"])
        if cfg["result_col"] is not None:
            result = cell_text(cfg["result_col"])
        else:
            result = _infer_toshin_result(summary)

        date_parts = date_and_docnum.split(" ", 1)
        decision_date = date_parts[0] if date_parts else ""
        document_number = date_parts[1] if len(date_parts) > 1 else ""

        rows.append({
            "case_ref_id": case_ref_id,
            "authority": authority,
            "kind": kind,
            "basis_laws": basis_laws,
            "council_name": council_name,
            "decision_date": decision_date,
            "document_number": document_number,
            "result": result,
            "summary": summary,
        })
    return rows


def load_existing() -> list[dict]:
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("cases", [])
    return []


def save(cases: list[PrecedentCase]) -> None:
    DATA_PATH.parent.mkdir(exist_ok=True)
    existing = {c["case_id"]: c for c in load_existing()}
    for c in cases:
        existing[c.case_id] = asdict(c)
    payload = {
        "source": "総務省 行政不服審査裁決・答申検索データベース (https://fufukudb.search.soumu.go.jp/koukai/Main)",
        "license_note": (
            "本データは公共データ利用規約(PDL1.0)に基づき、出典明記のうえ利用しています。"
            "各裁決・答申の全文の著作権は当該行政庁に帰属する場合があるため、全文は保存せず"
            "検索結果一覧の概要スニペットのみを保持しています。原文は各レコードのsource_urlを"
            "参照してください。"
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(existing),
        "cases": list(existing.values()),
    }
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"saved {len(existing)} cases -> {DATA_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages-per-query", type=int, default=6)
    parser.add_argument("--max-records", type=int, default=1000)
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    cases = scrape(
        max_pages_per_query=args.max_pages_per_query,
        max_records=args.max_records,
        headless=not args.headful,
    )
    save(cases)


if __name__ == "__main__":
    main()
