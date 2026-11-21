# Civic Lens Escrow（Solana）

行政不服審査ケースへの支援金を Solana 上で預かるエスクローです。結論が「認容」か「棄却」などに確定したら、それに応じて受益者へ支払うか、支援者へ返金します。
結論は、**総務省「行政不服審査裁決・答申検索データベース」**に載った裁決をもとに AI エージェント（`../resolution_agent.py`）が提案します。7日間の異議申立期間を経て確定します。

## 仕組み

```
create_case ─→ pledge_sol / pledge_usdc（UnderReview）
                     │
   ResolutionAgent が総務省DBの裁決を突合（主文のルール判定 × Gemini、両方一致したときだけ自動提案）
                     ↓
propose_resolution（Proposed）── 7日以内に支援者が challenge ─→ Challenged ─→ arbiter が arbitrate
                     │ 異議なし
                     ↓
finalize（誰でも実行可）→ Final
   ├ 認容・一部認容     → release（受益者へ SOL/USDC を全額送金）
   └ 棄却・却下・取下げ → refund（各支援者が自分の拠出分を回収）
期限＋540日が過ぎても結論が確定しない → refund
```

| アカウント | シード |
|---|---|
| Config | `["config"]`：admin、agent、arbiter、usdc_mint、case_count |
| Case | `["case", id(u64 LE)]`：SOL はこのPDAの残高（lamports）として直接保管 |
| Case vault | Case PDA が持つ USDC の ATA |
| Pledge | `["pledge", case, supporter]` |
| Professional | `["professional", wallet]`：登録済みの有資格者 |

## 弁護士法72条への配慮（設計上の制約）

- **受益者**は「申立人本人」か「admin が登録を確認した有資格者」に限られます。有資格者は、弁護士、特定行政書士、社会保険労務士、税理士で、それぞれの業務範囲内に限ります。無資格の第三者を受益者にするケースは、プログラムが作成を拒否します。
- **支援者には成功報酬がありません**（寄附です）。結論によって戻ってくるのは、各自が拠出した元本だけです。
- **運営は送金先を変更できません**。arbiter ができるのは結論を選ぶことだけで、送金先は受益者か拠出者本人のどちらかです。
- 個人情報はオンチェーンに記録しません。証拠と突合キーはハッシュで記録し、証拠のURIは削除できるストレージを指します。

> これは法的助言ではありません。公開前に弁護士に確認するか、グレーゾーン解消制度で照会することを推奨します。

## 開発

必要なもの：Solana CLI（Agave 4.x）、Anchor CLI 1.2.0、Node 20.18 以上。

```bash
anchor build --arch v1      # SBPF v3 は devnet/mainnet で未有効化のため v1 でビルドする
cargo test -p civic_lens_escrow --test escrow   # LiteSVM 統合テスト（時間経過も含む）
```

ローカルバリデータで確認する手順：

```bash
solana-test-validator --reset --bpf-program Ca7tu8tfHfJt4TgEzcPxKWtYiYUCigvWh6tr78Q7TXQ4 target/deploy/civic_lens_escrow.so
cd app && npm install
SOLANA_RPC_URL=http://127.0.0.1:8899 node scripts/bootstrap.mts --smoke   # Config初期化とスモークテスト
NEXT_PUBLIC_SOLANA_RPC_URL=http://127.0.0.1:8899 npm run dev
```

devnet にデプロイする場合：`anchor deploy --provider.cluster devnet` を実行したあと、`SOLANA_RPC_URL=https://api.devnet.solana.com node scripts/bootstrap.mts` で初期化します。本番では、`USDC_MINT`（USDC のミント）、`AGENT_PUBKEY`、`ARBITER_PUBKEY`（Squads マルチシグ）を指定してください。

プログラムを変更したあとは、`cd app && npm run sync-idl` で IDL と TypeScript の型をフロントエンドにコピーします。
