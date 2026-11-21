// エスクロープログラムの初期化（Config作成）＋任意でスモークテストを行うスクリプト
//
//   node scripts/bootstrap.mts                 # initialize のみ
//   node scripts/bootstrap.mts --smoke         # ケース登録・SOL/USDC支援まで実行して状態を表示
//
// 環境変数:
//   SOLANA_RPC_URL   既定: http://127.0.0.1:8899
//   ADMIN_KEYPAIR    既定: ~/.config/solana/id.json
//   USDC_MINT        未指定なら6桁のテスト用ミントを新規作成（localnet/devnet用）。本番はUSDCのミントを指定
//   AGENT_PUBKEY     ResolutionAgentの公開鍵（既定: admin）
//   ARBITER_PUBKEY   異議時の裁定者（Squadsマルチシグ推奨。既定: admin）
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { createHash } from "node:crypto";
import anchor from "@anchor-lang/core";
import { Connection, Keypair, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { createMint, getOrCreateAssociatedTokenAccount, mintTo, TOKEN_PROGRAM_ID } from "@solana/spl-token";
import idl from "../src/idl/civic_lens_escrow.json" with { type: "json" };

const { AnchorProvider, BN, Program, Wallet } = anchor;

const rpc = process.env.SOLANA_RPC_URL ?? "http://127.0.0.1:8899";
const keypairPath = process.env.ADMIN_KEYPAIR ?? `${homedir()}/.config/solana/id.json`;
const admin = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(readFileSync(keypairPath, "utf8"))));
const connection = new Connection(rpc, "confirmed");
const provider = new AnchorProvider(connection, new Wallet(admin), { commitment: "confirmed" });
const program = new Program(idl as anchor.Idl, provider);
const programId = new PublicKey(idl.address);

const configPda = PublicKey.findProgramAddressSync([Buffer.from("config")], programId)[0];
const casePda = (id: anchor.BN) =>
  PublicKey.findProgramAddressSync([Buffer.from("case"), id.toArrayLike(Buffer, "le", 8)], programId)[0];

async function ensureConfig() {
  const existing = await connection.getAccountInfo(configPda);
  if (existing) {
    const cfg = await (program.account as any).config.fetch(configPda);
    console.log(`Config は初期化済み: usdcMint=${cfg.usdcMint.toBase58()} caseCount=${cfg.caseCount}`);
    return cfg.usdcMint as PublicKey;
  }
  const usdcMint = process.env.USDC_MINT
    ? new PublicKey(process.env.USDC_MINT)
    : await createMint(connection, admin, admin.publicKey, null, 6);
  const agent = new PublicKey(process.env.AGENT_PUBKEY ?? admin.publicKey);
  const arbiter = new PublicKey(process.env.ARBITER_PUBKEY ?? admin.publicKey);
  const sig = await program.methods
    .initialize(agent, arbiter)
    .accountsPartial({ config: configPda, usdcMint, admin: admin.publicKey })
    .rpc();
  console.log(`initialize: ${sig}\n  usdcMint=${usdcMint.toBase58()} agent=${agent.toBase58()} arbiter=${arbiter.toBase58()}`);
  return usdcMint;
}

async function smoke(usdcMint: PublicKey) {
  const cfg = await (program.account as any).config.fetch(configPda);
  const id: anchor.BN = cfg.caseCount;
  const caseKey = casePda(id);
  const reviewKey = [...createHash("sha256").update("厚生労働大臣|生活保護変更決定|2026-01-15").digest()];

  await program.methods
    .createCase(
      "生活保護費の減額決定に対する審査請求（スモークテスト）",
      new Array(32).fill(7),
      "ipfs://example/evidence.pdf",
      reviewKey,
      admin.publicKey,
      new BN(Math.floor(Date.now() / 1000) + 60 * 86400),
    )
    .accountsPartial({ case: caseKey, usdcMint, professional: null, creator: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID })
    .rpc();

  await program.methods
    .pledgeSol(new BN(0.5 * LAMPORTS_PER_SOL))
    .accountsPartial({ case: caseKey, supporter: admin.publicKey })
    .rpc();

  const ata = await getOrCreateAssociatedTokenAccount(connection, admin, usdcMint, admin.publicKey);
  await mintTo(connection, admin, usdcMint, ata.address, admin, 10_000_000);
  await program.methods
    .pledgeUsdc(new BN(3_000_000))
    .accountsPartial({ case: caseKey, supporter: admin.publicKey, supporterAta: ata.address, usdcMint, tokenProgram: TOKEN_PROGRAM_ID })
    .rpc();

  const c = await (program.account as any).case.fetch(caseKey);
  console.log(
    `case #${c.id} ${caseKey.toBase58()}\n  title=${c.title}\n  status=${Object.keys(c.status)[0]}` +
      ` totalSol=${c.totalSol / LAMPORTS_PER_SOL} totalUsdc=${c.totalUsdc / 1e6} supporters=${c.supporterCount}`,
  );
}

const usdcMint = await ensureConfig();
if (process.argv.includes("--smoke")) await smoke(usdcMint);
