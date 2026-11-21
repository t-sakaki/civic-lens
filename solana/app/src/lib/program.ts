import { AnchorProvider, Program, BN } from "@anchor-lang/core";
import type { AnchorWallet } from "@solana/wallet-adapter-react";
import { Connection, PublicKey } from "@solana/web3.js";
import idl from "@/idl/civic_lens_escrow.json";
import type { CivicLensEscrow } from "@/idl/civic_lens_escrow";

export const PROGRAM_ID = new PublicKey(idl.address);

export function getProgram(connection: Connection, wallet: AnchorWallet) {
  const provider = new AnchorProvider(connection, wallet, { commitment: "confirmed" });
  return new Program<CivicLensEscrow>(idl as CivicLensEscrow, provider);
}

export const configPda = () => PublicKey.findProgramAddressSync([Buffer.from("config")], PROGRAM_ID)[0];

export const casePda = (id: BN | number) =>
  PublicKey.findProgramAddressSync(
    [Buffer.from("case"), new BN(id).toArrayLike(Buffer, "le", 8)],
    PROGRAM_ID,
  )[0];

export const professionalPda = (wallet: PublicKey) =>
  PublicKey.findProgramAddressSync([Buffer.from("professional"), wallet.toBuffer()], PROGRAM_ID)[0];

/** Python側 solana_escrow_client._normalize と同じ正規化（NFKC + 空白の畳み込み） */
const normalize = (s: string) => s.normalize("NFKC").split(/\s+/).filter(Boolean).join(" ");

export async function sha256(data: string | ArrayBuffer): Promise<number[]> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : new Uint8Array(data);
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
}

/** 総務省DB掲載裁決との突合キー: sha256(審査庁 | 処分の種類 | 申立日) */
export const reviewKeyHash = (authority: string, disposition: string, filedDate: string) =>
  sha256([authority, disposition, filedDate].map(normalize).join("|"));

export const OUTCOME_LABELS: Record<string, string> = {
  granted: "認容",
  partiallyGranted: "一部認容",
  dismissed: "棄却",
  rejected: "却下",
  withdrawn: "取下げ",
};

export const STATUS_LABELS: Record<string, string> = {
  underReview: "審理中（支援受付）",
  proposed: "結論提案済み（異議申立期間）",
  challenged: "異議申立中（arbiter判断待ち）",
  final: "確定",
};

export const enumKey = (v: object | null | undefined) => (v ? Object.keys(v)[0] : null);
