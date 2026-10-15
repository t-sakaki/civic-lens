"""対象機関の推定（ユーザーが対象機関を選んでいないとき）

対象機関が未選択のまま分析すると、従来は画面の既定値（先頭の選択肢）が送られ、
訴えの内容と無関係な機関（例: 愛知県議会）になることがあった。
このモジュールは、未選択のときに限り、訴えの内容から対象機関を推定する。

順序:
  1. 本文中の機関名・地名（収録済み機関のエイリアス）に一致すればそれを使う
  2. Gemini に、収録済み機関の中から最も適切なものを選ばせる（担当部署の推定も添える）
     市区町村が本文から分からない・収録外の場合は推定しない（authority_key を空にする）
  3. 推定できなければ空のまま返し、画面で市区町村の入力・機関の選択を求める

推定結果には必ず「AIの推定です。提出前に確認してください。」を添えて表示する。
請求する行政文書の特定ロジックには関与しない（対象機関のヒントを渡すだけ）。
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Optional

from gemini_models import generate as gemini_generate

INFERRED_NOTE = "AIの推定です。提出前に確認してください。"
GENERIC_NOTE = "AIの推定です。提出前に確認してください。（未収録の自治体のため、条例名・窓口は一般的な名称で表示しています）"
_PLACE_RE = re.compile(r"((?:東京都|北海道|(?:京都|大阪)府|[一-龥]{2,3}県)?[一-龥々ヶ]{1,6}?(?:市|区|町|村))(?:の|で|が|は|内|役所|役場)")


def _generic(municipality: str, department: str = "", reason: str = "", source: str = "") -> Optional[dict]:
    from ordinance_data import make_municipality_key, get_ordinance, addressee_name
    key = make_municipality_key(municipality)
    info = get_ordinance(key) if key else None
    if not info:
        return None
    r = _result(key, addressee_name(info), department, municipality, reason, source)
    r["note"] = GENERIC_NOTE
    r["generic"] = True
    return r


def _place_from_text(text: str) -> str:
    from ordinance_data import normalize_municipality_name
    for m in _PLACE_RE.finditer(text):
        n = normalize_municipality_name(m.group(1))
        if n:
            return n
    return ""
NEEDS_REGION_MESSAGE = "対象の市区町村が分からないため、対象機関を特定していません。市区町村を入力するか、対象機関を選んでください。"
UNKNOWN_MESSAGE = "内容から対象機関を推定できませんでした。対象機関を選んでください。"

PROMPT = """あなたは日本の情報公開制度に詳しいアシスタントです。
次の市民の訴えについて、行政文書開示請求の提出先として最も適切な機関を、候補の中から1つ選んでください。

ルール:
- 訴えに出てくる地名・施設・事業の主体から判断する（例: 市の公園の樹木伐採 → その市の公園・緑地担当部署を持つ市）
- 都道府県議会は、議会の活動（政務活動費・議事など）が対象のときだけ選ぶ
- 候補にない自治体の場合は authority_key を空文字にし、municipality に都道府県名つきの自治体名を書く（例: 東京都千代田区）
- 市区町村が訴えから読み取れない場合は authority_key も municipality も空文字にする
- 推測で無関係な機関を選ばない

【候補（key: 機関名）】
{options}

【市民の訴え】
{text}

次のJSONのみを出力:
{{"authority_key": "候補のkeyまたは空文字", "municipality": "訴えから読み取れる市区町村名（なければ空文字）", "department": "担当と思われる部署（例: 公園緑地課）", "reason": "選んだ理由（40文字程度）"}}
"""


def _options() -> tuple[str, dict]:
    from ordinance_data import AUTHORITIES
    lines = [f"{k}: {v.authority}" for k, v in AUTHORITIES.items()]
    return "\n".join(lines), AUTHORITIES


def _result(key: str = "", authority: str = "", department: str = "", municipality: str = "",
            reason: str = "", source: str = "") -> dict:
    inferred = bool(key)
    return {
        "inferred": inferred,
        "authority_key": key,
        "authority": authority,
        "department": department,
        "municipality": municipality,
        "reason": reason,
        "source": source,
        "needs_region": not inferred and not municipality,
        "note": INFERRED_NOTE if inferred else "",
        "message": "" if inferred else (NEEDS_REGION_MESSAGE if not municipality else UNKNOWN_MESSAGE),
    }


def infer_authority(text: str, client: Optional[Any] = None) -> dict:
    from ordinance_data import match_authority_by_text, get_ordinance, addressee_name

    text = (text or "").strip()
    if not text:
        return _result()
    # 1. 本文中の機関名・地名
    key = match_authority_by_text(text, default="")
    if key and get_ordinance(key):
        return _result(key, addressee_name(get_ordinance(key)), source="text")
    # 2. Gemini（未設定なら地名の抜き出しだけ試す）
    if client is None:
        place = _place_from_text(text)
        return (_generic(place, source="text_place") if place else None) or _result()
    options, authorities = _options()
    try:
        cfg = None
        try:
            from google.genai import types  # type: ignore
            cfg = types.GenerateContentConfig(response_mime_type="application/json")
        except Exception:  # noqa: BLE001
            pass
        resp = gemini_generate(client, "flash", PROMPT.format(options=options, text=text[:3000]),
                               config=cfg, timeout_s=20.0)
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.split("\n") if not l.startswith("```"))
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[authority_inference] 推定に失敗: {e}", file=sys.stderr)
        place = _place_from_text(text)
        return (_generic(place, source="text_place") if place else None) or _result()
    k = str(data.get("authority_key") or "").strip()
    muni = str(data.get("municipality") or "").strip()[:40]
    dept = str(data.get("department") or "").strip()[:40]
    reason = str(data.get("reason") or "").strip()[:120]
    info = authorities.get(k) if k else None
    if not info:
        # 収録外の自治体: 地名から汎用の機関を組み立てる（Geminiが地名を返さなければ本文から抜き出す）
        g = _generic(muni, dept, reason, "gemini_municipality") if muni else None
        if not g:
            place = _place_from_text(text)
            g = _generic(place, dept, reason, "text_place") if place else None
        return g or _result(municipality=muni, department=dept, reason=reason, source="gemini")
    return _result(k, addressee_name(info), dept, muni, reason, source="gemini")
