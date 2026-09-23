"""EAS スキーマの一度限りの実チェーン登録スクリプト。

実行前に環境変数 CHAIN_RPC_URL / CHAIN_PRIVATE_KEY を設定すること。
2つのスキーマ(開示請求証跡・SBTバッジ)をそれぞれ一度だけ登録し、
出力される schema_uid を EAS_SCHEMA_UID / BADGE_EAS_SCHEMA_UID に設定する。

使い方:
    export CHAIN_RPC_URL=https://sepolia.base.org
    export CHAIN_PRIVATE_KEY=0x...
    python scripts/register_eas_schema.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web3_attestation import EAS_SCHEMA_RAW
from web3_sbt import BADGE_SCHEMA_RAW
from web3_chain_client import register_schema

if __name__ == "__main__":
    for label, env_name, schema_raw in [
        ("開示請求証跡アテステーション", "EAS_SCHEMA_UID", EAS_SCHEMA_RAW),
        ("SBTバッジアテステーション", "BADGE_EAS_SCHEMA_UID", BADGE_SCHEMA_RAW),
    ]:
        result = register_schema(schema_raw, revocable=False)
        print(f"[{label}] 登録完了:")
        print(f"  schema_uid : {result['schema_uid']}")
        print(f"  tx_hash    : {result['tx_hash']}")
        print(f"  block      : {result['block_number']}")
        print(f"  -> 環境変数 {env_name} に設定してください。")
        print()
