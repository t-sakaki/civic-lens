# Civic Lens — Web3 Civic Reputation SBT (Soulbound Token / 譲渡不能バッジNFT)

import os
import json
import hashlib
import time
from typing import Optional, Dict, List
from datetime import datetime
from pydantic import BaseModel

DEFAULT_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "data")
if os.getenv("VERCEL"):
    STORAGE_DIR = "/tmp/civic_lens_data"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    SBT_STORAGE_PATH = os.path.join(STORAGE_DIR, "sbt_records.json")
else:
    SBT_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "sbt_records.json")

SBT_CONTRACT_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
CHAIN_NAME = "Base Mainnet (EVM L2)"
CHAIN_ID = 8453

BADGE_TYPES = {
    "first_request": {
        "name": "開示請求イニシエーター (First Disclosure)",
        "title": "Civic Lens 初動監視官",
        "description": "行政機関に対して初めて公式開示請求書を起草・行使した市民に授与される譲渡不能バッジ。",
        "color_primary": "#4F46E5",
        "color_secondary": "#818CF8",
        "icon": "🏛️",
        "tier": "Bronze",
    },
    "public_contributor": {
        "name": "集合知コントリビューター (Public Hero)",
        "title": "Civic Commons 知識共有者",
        "description": "自身の開示請求書をPublic公開し、地域社会全体の情報公開集合知の発展に寄与した功績を称える。",
        "color_primary": "#059669",
        "color_secondary": "#34D399",
        "icon": "🌐",
        "tier": "Silver",
    },
    "master_forker": {
        "name": "先例改善フォーカー (Precedent Innovator)",
        "title": "条例ハッカー",
        "description": "先行する請求書をフォーク・改良し、より精緻な文書請求モデルを確立した市民に授与。",
        "color_primary": "#D97706",
        "color_secondary": "#FBBF24",
        "icon": "⑂",
        "tier": "Gold",
    },
    "counter_champion": {
        "name": "不開示打破チャンピオン (Legal Defender)",
        "title": "権利防衛マスター",
        "description": "行政の不開示決定に対して法理と判例で対抗し、部分開示・完全開示を勝ち取った最高峰の栄誉。",
        "color_primary": "#DC2626",
        "color_secondary": "#F87171",
        "icon": "⚖️",
        "tier": "Platinum",
    }
}


class SBTRecord(BaseModel):
    token_id: str
    badge_key: str
    badge_name: str
    recipient_id: str
    wallet_address: str
    minted_at: str
    tx_hash: str
    svg_image: str
    contract_address: str = SBT_CONTRACT_ADDRESS
    chain: str = CHAIN_NAME
    is_soulbound: bool = True


def _ensure_storage():
    os.makedirs(os.path.dirname(SBT_STORAGE_PATH), exist_ok=True)
    if not os.path.exists(SBT_STORAGE_PATH):
        with open(SBT_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_sbt_records() -> Dict[str, dict]:
    _ensure_storage()
    try:
        with open(SBT_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_sbt_records(records: Dict[str, dict]):
    _ensure_storage()
    with open(SBT_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def generate_badge_svg(badge_key: str, recipient_id: str, minted_date: str) -> str:
    badge = BADGE_TYPES.get(badge_key, BADGE_TYPES["first_request"])
    c1 = badge["color_primary"]
    c2 = badge["color_secondary"]
    icon = badge["icon"]
    name = badge["name"]
    tier = badge["tier"]

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" width="100%" height="100%">
  <defs>
    <linearGradient id="grad_{badge_key}" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <filter id="glow_{badge_key}" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="8" stdDeviation="6" flood-color="{c1}" flood-opacity="0.4"/>
    </filter>
  </defs>
  <rect width="400" height="400" rx="24" fill="#0F172A"/>
  <rect x="15" y="15" width="370" height="370" rx="20" fill="none" stroke="url(#grad_{badge_key})" stroke-width="3" stroke-dasharray="6,6" opacity="0.6"/>
  <circle cx="200" cy="150" r="80" fill="url(#grad_{badge_key})" filter="url(#glow_{badge_key})"/>
  <text x="200" y="172" font-size="64" text-anchor="middle">{icon}</text>
  <rect x="130" y="245" width="140" height="26" rx="13" fill="#1E293B" stroke="{c2}" stroke-width="1.5"/>
  <text x="200" y="262" font-family="monospace, sans-serif" font-size="12" font-weight="bold" fill="{c2}" text-anchor="middle">{tier.upper()} SBT</text>
  <text x="200" y="300" font-family="sans-serif" font-size="16" font-weight="bold" fill="#F8FAFC" text-anchor="middle">{name[:22]}</text>
  <text x="200" y="328" font-family="monospace, sans-serif" font-size="13" fill="#94A3B8" text-anchor="middle">Civic ID: {recipient_id}</text>
  <text x="200" y="355" font-family="monospace, sans-serif" font-size="11" fill="#64748B" text-anchor="middle">Minted: {minted_date} | ERC-5192</text>
</svg>'''
    return svg


def mint_sbt(
    recipient_id: str,
    badge_key: str = "first_request",
    wallet_address: Optional[str] = None
) -> SBTRecord:
    if badge_key not in BADGE_TYPES:
        badge_key = "first_request"

    badge = BADGE_TYPES[badge_key]
    token_id = f"sbt-{badge_key[:3]}-{hashlib.sha256(f'{recipient_id}:{time.time()}'.encode()).hexdigest()[:8]}"
    minted_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    tx_hash = "0x" + hashlib.sha256(f"sbt:{token_id}:{minted_at}".encode()).hexdigest()
    
    wallet = wallet_address or f"0x{hashlib.sha256(recipient_id.encode()).hexdigest()[:40]}"
    svg = generate_badge_svg(badge_key, recipient_id, minted_at)

    record = SBTRecord(
        token_id=token_id,
        badge_key=badge_key,
        badge_name=badge["name"],
        recipient_id=recipient_id,
        wallet_address=wallet,
        minted_at=minted_at,
        tx_hash=tx_hash,
        svg_image=svg,
        contract_address=SBT_CONTRACT_ADDRESS,
        chain=CHAIN_NAME,
        is_soulbound=True,
    )

    records = _load_sbt_records()
    records[token_id] = record.model_dump()
    _save_sbt_records(records)

    return record


def get_sbt_metadata(token_id: str) -> Optional[dict]:
    records = _load_sbt_records()
    if token_id not in records:
        return None
    r = records[token_id]
    badge = BADGE_TYPES.get(r["badge_key"], {})
    
    return {
        "name": r["badge_name"],
        "description": badge.get("description", "Civic Lens 譲渡不能市民バッジ"),
        "image_data": r["svg_image"],
        "external_url": f"https://civic-lens-liart.vercel.app/sbt/{token_id}",
        "attributes": [
            {"trait_type": "Recipient", "value": r["recipient_id"]},
            {"trait_type": "Wallet", "value": r["wallet_address"]},
            {"trait_type": "Tier", "value": badge.get("tier", "Bronze")},
            {"trait_type": "Soulbound", "value": "True (Locked)"},
            {"trait_type": "Mint Date", "value": r["minted_at"]},
            {"trait_type": "Chain", "value": r["chain"]},
        ]
    }


def get_user_passport(recipient_id_or_wallet: str) -> List[SBTRecord]:
    records = _load_sbt_records()
    results = []
    target = recipient_id_or_wallet.lower()
    for r in records.values():
        if r["recipient_id"].lower() == target or r["wallet_address"].lower() == target:
            results.append(SBTRecord(**r))
    return results


def list_available_badges() -> List[dict]:
    return [
        {
            "badge_key": k,
            "name": v["name"],
            "title": v["title"],
            "description": v["description"],
            "tier": v["tier"],
            "icon": v["icon"],
        }
        for k, v in BADGE_TYPES.items()
    ]
