# Civic Lens — Web3 Civic Bounty（開示請求コピー代・調査費分散型ファンディング）
#
# 出資(pledge)は実際の資金移動を伴うため、ユーザー申告のamount_jpyをそのまま記録せず、
# ユーザーのウォレットからエスクローアドレスへの実USDC送金トランザクション(tx_hash)を
# web3_chain_client.verify_erc20_transfer() でオンチェーン検証してから記録する。
# 検証に失敗した場合は例外を送出し、疑似tx_hashへのフォールバックは行わない。

import os
import json
import hashlib
import time
from typing import Optional, Dict, List
from datetime import datetime
from pydantic import BaseModel

from web3_chain_client import verify_erc20_transfer, ChainClientNotConfigured

DEFAULT_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "data")
if os.getenv("VERCEL"):
    STORAGE_DIR = "/tmp/civic_lens_data"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    BOUNTY_STORAGE_PATH = os.path.join(STORAGE_DIR, "bounties.json")
else:
    BOUNTY_STORAGE_PATH = os.path.join(DEFAULT_STORAGE_DIR, "bounties.json")

DEFAULT_TOKEN = "USDC"
USDC_DECIMALS = 6


def _escrow_address() -> str:
    """出資金の実際の着金先ウォレット。運営が管理する実アドレスを環境変数で設定する。"""
    address = os.getenv("BOUNTY_ESCROW_ADDRESS")
    if not address:
        raise ChainClientNotConfigured(
            "環境変数 BOUNTY_ESCROW_ADDRESS が未設定です。実USDCエスクローの受取先"
            "ウォレットアドレスを設定してください。"
        )
    return address


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
    escrow_address: str
    payout_note: str = "資金の解放(payout)は運営による実USDC送金で行われ、本システムは自動送金しません。"


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
        requester_wallet=requester_wallet or "",
        pledges=[],
        created_at=now_iso,
        escrow_address=_escrow_address(),
    )

    records = _load_bounties()
    records[bounty_id] = campaign.model_dump()
    records[f"by_record:{record_id}"] = campaign.model_dump()
    _save_bounties(records)

    return campaign


def pledge_bounty(
    bounty_id_or_record_id: str,
    wallet_address: str,
    tx_hash: str,
    amount_jpy: int = 500,
    backer_id: str = "市民",
    message: str = "応援しています！",
    jpy_per_usdc: float = 150.0,
) -> BountyCampaign:
    """出資を記録する。

    wallet_address からエスクローアドレスへの実USDC送金である tx_hash を
    オンチェーンで検証し、検証されたオンチェーン金額のみを記録する。
    amount_jpy はUI表示用の目安換算にのみ使い、検証済みオンチェーン金額を上書きしない。
    """
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

    claimed_amount_usdc = round(amount_jpy / jpy_per_usdc, 2)
    min_amount_units = int(claimed_amount_usdc * (10 ** USDC_DECIMALS))

    verification = verify_erc20_transfer(
        tx_hash=tx_hash,
        expected_from=wallet_address,
        expected_to=campaign.escrow_address,
        min_amount_units=min_amount_units,
    )
    verified_amount_usdc = verification["amount_units"] / (10 ** USDC_DECIMALS)
    verified_amount_jpy = round(verified_amount_usdc * jpy_per_usdc)

    pledge = Pledge(
        backer_id=backer_id,
        wallet_address=wallet_address,
        amount_usdc=verified_amount_usdc,
        amount_jpy=verified_amount_jpy,
        tx_hash=tx_hash,
        timestamp=datetime.utcnow().isoformat() + "Z",
        message=message,
    )

    campaign.pledges.append(pledge)
    campaign.current_amount_jpy += verified_amount_jpy
    campaign.current_amount_usdc = round(campaign.current_amount_usdc + verified_amount_usdc, 2)

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
