"""Civic Lens — ENS（Ethereum Name Service）逆引き

投げ銭リーダーボード・急上昇ランキングで、ウォレットアドレスの代わりに
`peikun.eth` のような人間が読める名前を表示するための読み取り専用ユーティリティ。

ENSレコードはEthereumメインネット上にあるため、Base Sepolia/Base用のRPC
（CHAIN_RPC_URL）とは別のメインネット読み取り専用RPCに接続する。秘密鍵は使わない。
"""
import os
from functools import lru_cache
from typing import Optional

from web3 import Web3

ENS_RPC_URL = os.getenv("ENS_RPC_URL", "https://ethereum-rpc.publicnode.com")

_w3: Optional[Web3] = None


def _client() -> Web3:
    global _w3
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider(ENS_RPC_URL))
    return _w3


@lru_cache(maxsize=256)
def resolve_ens_name(address: str) -> Optional[str]:
    """ベストエフォートの逆引き。解決できなければ None（例外は投げない）。"""
    try:
        return _client().ens.name(Web3.to_checksum_address(address))
    except Exception:
        return None
