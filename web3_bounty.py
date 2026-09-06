# Civic Lens — Web3 Civic Bounty（開示請求コピー代・調査費分散型ファンディング）

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
    BOUNTY_STORAGE_PATH = os.path.join(STORAGE_DIR, "bounties.json")
else:
    BOUNTY_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "bounties.json")

ESCROW_CONTRACT_ADDRESS = "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE"
DEFAULT_TOKEN = "USDC"


class Pledge(BaseModel):
    backer_id: str
    wallet_address: str
    amount_usdc: float
    amount_jpy: int
    tx_hash: str
    timestamp: str
    message: Optional[str] = ""


class BountyCampaign(BaseModel):
    bounty_id: str
    record_id: str
    title: str
    authority: str
    target_amount_usdc: float
    target_amount_jpy: int
    current_amount_usdc: float = 0.0
    current_amount_jpy: int = 0
    status: str = "funding"
    requester_wallet: str
    pledges: List[Pledge] = []
    created_at: str
    claimed_at: Optional[str] = None
    proof_document_cid: Optional[str] = None
    escrow_address: str = ESCROW_CONTRACT_ADDRESS


def _ensure_storage():
    os.makedirs(os.path.dirname(BOUNTY_STORAGE_PATH), exist_ok=True)
    if not os.path.exists(BOUNTY_STORAGE_PATH):
        with open(BOUNTY_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_bounties() -> Dict[str, dict]:
    _ensure_storage()
    try:
        with open(BOUNTY_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _save_bounties(records: Dict[str, dict]):
    _ensure_storage()
    with open(BOUNTY_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def create_bounty(
    record_id: str,
    title: str,
    authority: str,
    target_pages: int = 50,
    cost_per_page_jpy: int = 20,
    requester_wallet: Optional[str] = None,
) -> BountyCampaign:
    target_jpy = (target_pages * cost_per_page_jpy) + 500
    target_usdc = round(target_jpy / 150.0, 2)
    bounty_id = f"bnt-{record_id[:8] if record_id else hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"
    
    now_iso = datetime.utcnow().isoformat() + "Z"
    campaign = BountyCampaign(
        bounty_id=bounty_id,
        record_id=record_id,
        title=title or f"{authority} 開示文書コピー代支援プール",
        authority=authority,
        target_amount_usdc=target_usdc,
        target_amount_jpy=target_jpy,
        current_amount_usdc=0.0,
        current_amount_jpy=0,
        status="funding",
        requester_wallet=requester_wallet or "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        pledges=[],
        created_at=now_iso,
    )

    records = _load_bounties()
    records[bounty_id] = campaign.model_dump()
    records[f"by_record:{record_id}"] = campaign.model_dump()
    _save_bounties(records)

    return campaign


def pledge_bounty(
    bounty_id_or_record_id: str,
    amount_jpy: int = 500,
    backer_id: str = "市民",
    wallet_address: Optional[str] = None,
    message: str = "応援しています！"
) -> BountyCampaign:
    records = _load_bounties()
    key = bounty_id_or_record_id
    if key not in records:
        key = f"by_record:{bounty_id_or_record_id}"
    if key not in records:
        campaign = create_bounty(
            record_id=bounty_id_or_record_id,
            title=f"開示請求コピー代支援 ({bounty_id_or_record_id})",
            authority="自治体",
        )
        records = _load_bounties()
        b_id = campaign.bounty_id
    else:
        b_id = records[key]["bounty_id"]

    data = records[b_id]
    campaign = BountyCampaign(**data)

    amount_usdc = round(amount_jpy / 150.0, 2)
    tx_hash = "0x" + hashlib.sha256(f"{b_id}:{time.time()}:{amount_jpy}".encode()).hexdigest()

    pledge = Pledge(
        backer_id=backer_id,
        wallet_address=wallet_address or f"0x{hashlib.sha256(backer_id.encode()).hexdigest()[:40]}",
        amount_usdc=amount_usdc,
        amount_jpy=amount_jpy,
        tx_hash=tx_hash,
        timestamp=datetime.utcnow().isoformat() + "Z",
        message=message,
    )

    campaign.pledges.append(pledge)
    campaign.current_amount_jpy += amount_jpy
    campaign.current_amount_usdc = round(campaign.current_amount_usdc + amount_usdc, 2)

    if campaign.current_amount_jpy >= campaign.target_amount_jpy and campaign.status == "funding":
        campaign.status = "funded"

    records[campaign.bounty_id] = campaign.model_dump()
    records[f"by_record:{campaign.record_id}"] = campaign.model_dump()
    _save_bounties(records)

    return campaign


def claim_bounty(
    bounty_id_or_record_id: str,
    proof_document_cid: str,
    requester_wallet: Optional[str] = None
) -> BountyCampaign:
    records = _load_bounties()
    key = bounty_id_or_record_id
    if key not in records:
        key = f"by_record:{bounty_id_or_record_id}"
    if key not in records:
        raise ValueError("Bounty campaign not found")

    b_id = records[key]["bounty_id"]
    campaign = BountyCampaign(**records[b_id])

    campaign.status = "claimed"
    campaign.proof_document_cid = proof_document_cid
    campaign.claimed_at = datetime.utcnow().isoformat() + "Z"
    if requester_wallet:
        campaign.requester_wallet = requester_wallet

    records[campaign.bounty_id] = campaign.model_dump()
    records[f"by_record:{campaign.record_id}"] = campaign.model_dump()
    _save_bounties(records)

    return campaign


def get_bounty(bounty_id_or_record_id: str) -> Optional[BountyCampaign]:
    records = _load_bounties()
    if bounty_id_or_record_id in records:
        return BountyCampaign(**records[bounty_id_or_record_id])
    key = f"by_record:{bounty_id_or_record_id}"
    if key in records:
        return BountyCampaign(**records[key])
    return None


def list_all_bounties() -> List[BountyCampaign]:
    records = _load_bounties()
    result = []
    for k, v in records.items():
        if not k.startswith("by_record:"):
            result.append(BountyCampaign(**v))
    return sorted(result, key=lambda x: x.created_at, reverse=True)
