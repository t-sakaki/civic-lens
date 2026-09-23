"""外部（電子申請・郵送・窓口）で提出した開示請求を、EASでオンチェーンに記録するスクリプト。

オンチェーンに載るもの:
  - 実施機関（例: 愛知県知事）、根拠条例、記録時刻
  - 請求書全文のハッシュ（氏名・住所はハッシュの中に閉じ込められ、外からは読めない）
  - --public 指定時のみ: 請求する公文書の特定内容（平文。削除できないため記録前に確認する）

事前に .env の CHAIN_RPC_URL / CHAIN_PRIVATE_KEY / EAS_SCHEMA_UID を設定すること。

使い方:
    python scripts/attest_disclosure_request.py \\
        --authority 愛知県知事 --legal-basis 愛知県情報公開条例 \\
        --content-file 申請内容.txt \\
        --requested-documents "アジア競技大会・…の庁内決裁、協議記録" --public

    --content-file には、電子申請の申請内容（到達番号・受付日時を含めると、請求日の起点として残せる）を
    テキストで保存したものを指定する。同じファイルで後から verify すれば同一性を検証できる。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from web3_attestation import (  # noqa: E402
    build_verification_kit,
    compute_document_hash,
    issue_attestation,
    normalize_requested_documents,
    scan_personal_info,
    verify_attestation,
)


def main():
    parser = argparse.ArgumentParser(description="外部で提出した開示請求をオンチェーンに記録する")
    parser.add_argument("--authority", required=True, help="実施機関（例: 愛知県知事）")
    parser.add_argument("--legal-basis", required=True, help="根拠条例（例: 愛知県情報公開条例）")
    parser.add_argument("--content-file", required=True, help="請求書（申請内容）全文のテキストファイル")
    parser.add_argument("--requested-documents", default="", help="請求する公文書の特定内容")
    parser.add_argument("--public", action="store_true", help="請求文書の特定内容を平文でオンチェーンに記録する")
    parser.add_argument("--title", default="開示請求書")
    parser.add_argument("--record-id", default=None)
    parser.add_argument("--yes", action="store_true", help="確認プロンプトを省略する")
    parser.add_argument("--verify", metavar="UID_OR_RECORD_ID", help="記録済みの証明と content-file の同一性を検証する")
    args = parser.parse_args()

    with open(args.content_file, encoding="utf-8") as f:
        content = f.read()

    if args.verify:
        print(json.dumps(verify_attestation(args.verify, content), ensure_ascii=False, indent=2))
        return

    public_docs = normalize_requested_documents(args.requested_documents) if args.public else ""
    warnings = scan_personal_info(public_docs) if public_docs else []

    print("=== オンチェーンに記録する内容（記録後は削除できません） ===")
    print(f"実施機関          : {args.authority}")
    print(f"根拠条例          : {args.legal_basis}")
    print(f"請求文書の特定内容: {public_docs or '（非公開：ハッシュのみ）'}")
    print(f"請求書全文のハッシュ: {compute_document_hash(content)}")
    if warnings:
        print("\n⚠ 個人情報の可能性がある記述が見つかりました:")
        for w in warnings:
            print(f"  - {w['type']}: {w['match']}")

    if warnings and args.yes:
        print("\n警告があるため --yes では記録しません。内容を確認のうえ、--yes を外して対話的に実行してください。")
        sys.exit(1)
    if not args.yes:
        answer = input("\nこの内容でオンチェーンに記録しますか？ [y/N]: ").strip().lower()
        if answer != "y":
            print("中止しました。")
            return

    record = issue_attestation(
        record_id=args.record_id or f"req-{os.urandom(4).hex()}",
        title=args.title,
        content=content,
        authority=args.authority,
        legal_basis=args.legal_basis,
        requested_documents=args.requested_documents,
        publish_plaintext=args.public,
        acknowledge_warnings=True,  # 上で内容と警告を提示し、本人が確認済み
    )
    print(json.dumps(record.model_dump(), ensure_ascii=False, indent=2))

    # 検証キット（原本を含む）を content-file の隣に保存する。サーバー側の索引には原本を残さない
    kit_path = os.path.splitext(args.content_file)[0] + ".verification-kit.json"
    with open(kit_path, "w", encoding="utf-8") as f:
        json.dump(build_verification_kit(record, content), f, ensure_ascii=False, indent=2)
    print(f"\n検証キットを保存しました: {kit_path}（個人情報を含むため取り扱いに注意）")


if __name__ == "__main__":
    main()
