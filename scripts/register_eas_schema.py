"""EAS スキーマの一度限りの実チェーン登録スクリプト。

登録するスキーマを名前で指定する（指定なしでは何も登録しない）。
  request   開示請求証跡            -> EAS_SCHEMA_UID
  badge     SBTバッジ               -> BADGE_EAS_SCHEMA_UID
  extension 延長・期限の特例の通知  -> EXTENSION_SCHEMA_UID

すでに環境変数にUIDが設定されているスキーマは再登録しない（同じ定義の再登録はチェーン側で
失敗し、ガス代だけがかかる）。上書きしたいときだけ --force を付ける。
CHAIN_RPC_URL / CHAIN_PRIVATE_KEY は、シェルの環境変数かプロジェクトの .env から読み込む。

使い方:
    python scripts/register_eas_schema.py extension
    python scripts/register_eas_schema.py request badge --yes
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from web3_attestation import EAS_SCHEMA_RAW
from web3_sbt import BADGE_SCHEMA_RAW
from ledger_extensions import EXTENSION_SCHEMA_RAW
from web3_chain_client import register_schema, get_web3, get_notary_account

# 名前 -> (表示名, 環境変数名, スキーマ定義, 撤回可能か)
# 延長・期限の特例の通知は、請求者が誤記を撤回できるよう revocable=True で登録する
SCHEMAS = {
    "request": ("開示請求証跡アテステーション", "EAS_SCHEMA_UID", EAS_SCHEMA_RAW, False),
    "badge": ("SBTバッジアテステーション", "BADGE_EAS_SCHEMA_UID", BADGE_SCHEMA_RAW, False),
    "extension": ("延長・期限の特例アテステーション", "EXTENSION_SCHEMA_UID", EXTENSION_SCHEMA_RAW, True),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="+", choices=sorted(SCHEMAS), help="登録するスキーマ")
    parser.add_argument("--force", action="store_true", help="環境変数にUIDが設定済みでも登録する")
    parser.add_argument("--yes", action="store_true", help="確認の質問を省略する")
    args = parser.parse_args(argv)

    targets = []
    for name in dict.fromkeys(args.names):
        label, env_name, schema_raw, revocable = SCHEMAS[name]
        if os.getenv(env_name) and not args.force:
            print(f"[{label}] {env_name} が設定済みのため登録しません（{os.getenv(env_name)}）。上書きするなら --force。")
            continue
        targets.append((label, env_name, schema_raw, revocable))
    if not targets:
        return 0

    w3 = get_web3()
    account, _ = get_notary_account(w3)
    print(f"チェーンID {w3.eth.chain_id} / 送信元 {account.address} に、次のスキーマを登録します:")
    for label, _, schema_raw, revocable in targets:
        print(f"  - {label}（撤回{'可' if revocable else '不可'}）: {schema_raw}")
    if not args.yes and input("実行しますか？ [y/N] ").strip().lower() != "y":
        print("中止しました。")
        return 1

    for label, env_name, schema_raw, revocable in targets:
        result = register_schema(schema_raw, revocable=revocable)
        print(f"[{label}] 登録完了:")
        print(f"  schema_uid : {result['schema_uid']}")
        print(f"  tx_hash    : {result['tx_hash']}")
        print(f"  block      : {result['block_number']}")
        print(f"  -> 環境変数 {env_name} に設定してください。")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
