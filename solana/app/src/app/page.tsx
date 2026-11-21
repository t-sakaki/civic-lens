"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { BN } from "@anchor-lang/core";
import { useAnchorWallet, useConnection } from "@solana/wallet-adapter-react";
import { LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { getAssociatedTokenAddressSync, TOKEN_PROGRAM_ID } from "@solana/spl-token";
import {
  casePda,
  configPda,
  enumKey,
  getProgram,
  OUTCOME_LABELS,
  professionalPda,
  reviewKeyHash,
  sha256,
  STATUS_LABELS,
} from "@/lib/program";

// ウォレットボタンはSSR不可（window依存）
const WalletMultiButton = dynamic(
  async () => (await import("@solana/wallet-adapter-react-ui")).WalletMultiButton,
  { ssr: false },
);

const USDC_DECIMALS = 6;
const CHALLENGE_PERIOD_SECS = 7 * 24 * 60 * 60;

type Program = ReturnType<typeof getProgram>;
type CaseAccount = Awaited<ReturnType<Program["account"]["case"]["fetch"]>>;
type CaseRow = { publicKey: PublicKey; account: CaseAccount };

export default function Home() {
  const { connection } = useConnection();
  const wallet = useAnchorWallet();
  const program = useMemo(() => (wallet ? getProgram(connection, wallet) : null), [connection, wallet]);

  const [cases, setCases] = useState<CaseRow[]>([]);
  const [usdcMint, setUsdcMint] = useState<PublicKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!program) return;
    try {
      const config = await program.account.config.fetch(configPda());
      setUsdcMint(config.usdcMint);
      const all = await program.account.case.all();
      setCases(all.sort((a, b) => b.account.id.cmp(a.account.id)));
    } catch (e) {
      setError(`プログラムの状態を読めませんでした（未デプロイ・未初期化の可能性）: ${e}`);
    }
  }, [program]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const sig = await fn();
      setMessage(`${label}: 送信しました (${sig.slice(0, 16)}…)`);
      await refresh();
    } catch (e) {
      setError(`${label}に失敗しました: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main>
      <header>
        <div>
          <h1>Civic Lens Escrow</h1>
          <div className="note">行政不服審査ケースの支援金エスクロー（Solana）</div>
        </div>
        <WalletMultiButton />
      </header>

      <p className="note">
        支援金は寄附です。認容・一部認容が確定すると受益者（申立人本人または登録済みの有資格者）へ、
        棄却・却下・取下げの場合は各支援者へ全額返金されます。結論は総務省「行政不服審査裁決・答申検索データベース」の掲載裁決をもとに
        AIエージェントが提案し、7日間の異議申立期間を経て確定します。支援者への成功報酬はありません。
      </p>

      {message && <p className="note">{message}</p>}
      {error && <p className="error">{error}</p>}

      {!wallet ? (
        <p className="note">ウォレット（Phantom等）を接続してください。</p>
      ) : (
        <>
          <CreateCaseForm
            busy={busy}
            onSubmit={(input) =>
              run("ケース登録", async () => {
                const p = program!;
                const config = await p.account.config.fetch(configPda());
                const id = config.caseCount;
                const beneficiary = input.beneficiary ?? wallet.publicKey;
                const isSelf = beneficiary.equals(wallet.publicKey);
                return p.methods
                  .createCase(
                    input.title,
                    input.evidenceHash,
                    input.evidenceUri,
                    input.reviewKeyHash,
                    beneficiary,
                    new BN(Math.floor(Date.now() / 1000) + input.deadlineDays * 86400),
                  )
                  .accountsPartial({
                    case: casePda(id),
                    usdcMint: config.usdcMint,
                    professional: isSelf ? null : professionalPda(beneficiary),
                    creator: wallet.publicKey,
                    tokenProgram: TOKEN_PROGRAM_ID,
                  })
                  .rpc();
              })
            }
          />

          <h2>ケース一覧</h2>
          {cases.length === 0 && <p className="note">まだケースがありません。</p>}
          {cases.map((c) => (
            <CaseCard
              key={c.publicKey.toBase58()}
              row={c}
              busy={busy}
              wallet={wallet.publicKey}
              onPledgeSol={(sol) =>
                run("SOL支援", () =>
                  program!.methods
                    .pledgeSol(new BN(Math.round(sol * LAMPORTS_PER_SOL)))
                    .accountsPartial({ case: c.publicKey, supporter: wallet.publicKey })
                    .rpc(),
                )
              }
              onPledgeUsdc={(usdc) =>
                run("USDC支援", () =>
                  program!.methods
                    .pledgeUsdc(new BN(Math.round(usdc * 10 ** USDC_DECIMALS)))
                    .accountsPartial({
                      case: c.publicKey,
                      supporter: wallet.publicKey,
                      supporterAta: getAssociatedTokenAddressSync(usdcMint!, wallet.publicKey),
                      usdcMint: usdcMint!,
                      tokenProgram: TOKEN_PROGRAM_ID,
                    })
                    .rpc(),
                )
              }
              onChallenge={() =>
                run("異議申立", () =>
                  program!.methods.challenge().accountsPartial({ case: c.publicKey, supporter: wallet.publicKey }).rpc(),
                )
              }
              onFinalize={() =>
                run("確定", () => program!.methods.finalize().accountsPartial({ case: c.publicKey }).rpc())
              }
              onRelease={() =>
                run("受益者への解放", () =>
                  program!.methods
                    .release()
                    .accountsPartial({
                      case: c.publicKey,
                      beneficiary: c.account.beneficiary,
                      usdcMint: usdcMint!,
                      payer: wallet.publicKey,
                      tokenProgram: TOKEN_PROGRAM_ID,
                    })
                    .rpc(),
                )
              }
              onRefund={() =>
                run("返金", () =>
                  program!.methods
                    .refund()
                    .accountsPartial({
                      case: c.publicKey,
                      supporter: wallet.publicKey,
                      usdcMint: usdcMint!,
                      tokenProgram: TOKEN_PROGRAM_ID,
                    })
                    .rpc(),
                )
              }
            />
          ))}
        </>
      )}
    </main>
  );
}

type CreateCaseInput = {
  title: string;
  evidenceHash: number[];
  evidenceUri: string;
  reviewKeyHash: number[];
  beneficiary: PublicKey | null;
  deadlineDays: number;
};

function CreateCaseForm({ busy, onSubmit }: { busy: boolean; onSubmit: (i: CreateCaseInput) => void }) {
  const [title, setTitle] = useState("");
  const [authority, setAuthority] = useState("");
  const [disposition, setDisposition] = useState("");
  const [filedDate, setFiledDate] = useState("");
  const [evidenceUri, setEvidenceUri] = useState("");
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [beneficiary, setBeneficiary] = useState("");
  const [deadlineDays, setDeadlineDays] = useState(60);
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async () => {
    setFormError(null);
    if (new TextEncoder().encode(title).length > 128) return setFormError("タイトルは128バイト（日本語約40文字）以内です");
    if (!title || !authority || !disposition || !filedDate) return setFormError("必須項目を入力してください");
    let beneficiaryKey: PublicKey | null = null;
    if (beneficiary.trim()) {
      try {
        beneficiaryKey = new PublicKey(beneficiary.trim());
      } catch {
        return setFormError("受益者のアドレスが不正です");
      }
    }
    onSubmit({
      title,
      evidenceHash: evidenceFile ? await sha256(await evidenceFile.arrayBuffer()) : new Array(32).fill(0),
      evidenceUri,
      reviewKeyHash: await reviewKeyHash(authority, disposition, filedDate),
      beneficiary: beneficiaryKey,
      deadlineDays,
    });
  };

  return (
    <section className="card">
      <h2 style={{ marginTop: 0 }}>ケースを登録する</h2>
      <p className="note">
        氏名・住所などの個人情報は入力しないでください。審査庁・処分の種類・審査請求日はハッシュ化して記録され、
        総務省DBの裁決と突合するためだけに使われます。証拠ファイルもハッシュのみがオンチェーンに記録されます。
      </p>
      <div className="grid">
        <label>タイトル（公開）<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>審査庁<input value={authority} onChange={(e) => setAuthority(e.target.value)} placeholder="例: 厚生労働大臣" /></label>
        <label>処分の種類<input value={disposition} onChange={(e) => setDisposition(e.target.value)} placeholder="例: 生活保護変更決定" /></label>
        <label>審査請求日<input type="date" value={filedDate} onChange={(e) => setFiledDate(e.target.value)} /></label>
        <label>証拠のURI（削除可能なストレージ）<input value={evidenceUri} onChange={(e) => setEvidenceUri(e.target.value)} /></label>
        <label>証拠ファイル（ハッシュのみ記録）<input type="file" onChange={(e) => setEvidenceFile(e.target.files?.[0] ?? null)} /></label>
        <label>支援受付日数<input type="number" min={1} value={deadlineDays} onChange={(e) => setDeadlineDays(Number(e.target.value))} /></label>
        <label>受益者（空欄=自分。他人の場合は登録済み有資格者のみ）<input value={beneficiary} onChange={(e) => setBeneficiary(e.target.value)} /></label>
      </div>
      {formError && <p className="error">{formError}</p>}
      <div className="row">
        <button disabled={busy} onClick={submit}>登録</button>
      </div>
    </section>
  );
}

function CaseCard(props: {
  row: CaseRow;
  busy: boolean;
  wallet: PublicKey;
  onPledgeSol: (sol: number) => void;
  onPledgeUsdc: (usdc: number) => void;
  onChallenge: () => void;
  onFinalize: () => void;
  onRelease: () => void;
  onRefund: () => void;
}) {
  const { account: c } = props.row;
  const [sol, setSol] = useState(0.1);
  const [usdc, setUsdc] = useState(1);
  const status = enumKey(c.status)!;
  const outcome = enumKey(c.outcome);
  const now = Date.now() / 1000;
  const open = status === "underReview" && now <= c.deadline.toNumber();
  const challengeEnds = c.proposedAt.toNumber() + CHALLENGE_PERIOD_SECS;
  const grants = outcome === "granted" || outcome === "partiallyGranted";

  return (
    <article className="card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>#{c.id.toString()} {c.title}</strong>
        <span className="badge">{STATUS_LABELS[status]}{outcome ? `：${OUTCOME_LABELS[outcome]}` : ""}</span>
      </div>
      <div className="note">
        支援総額 {(c.totalSol.toNumber() / LAMPORTS_PER_SOL).toFixed(3)} SOL ／{" "}
        {(c.totalUsdc.toNumber() / 10 ** USDC_DECIMALS).toFixed(2)} USDC ・ 支援者 {c.supporterCount} 人 ・ 受付期限{" "}
        {new Date(c.deadline.toNumber() * 1000).toLocaleDateString("ja-JP")}
      </div>
      {c.sourceUri && <div className="mono">根拠: {c.sourceUri}</div>}
      {status === "proposed" && (
        <div className="note">異議申立期限: {new Date(challengeEnds * 1000).toLocaleString("ja-JP")}</div>
      )}

      <div className="row">
        {open && (
          <>
            <label>SOL<input type="number" step="0.01" min={0} value={sol} onChange={(e) => setSol(Number(e.target.value))} /></label>
            <button disabled={props.busy || sol <= 0} onClick={() => props.onPledgeSol(sol)}>SOLで支援</button>
            <label>USDC<input type="number" step="1" min={0} value={usdc} onChange={(e) => setUsdc(Number(e.target.value))} /></label>
            <button disabled={props.busy || usdc <= 0} onClick={() => props.onPledgeUsdc(usdc)}>USDCで支援</button>
          </>
        )}
        {status === "proposed" && now <= challengeEnds && (
          <button className="secondary" disabled={props.busy} onClick={props.onChallenge}>結論に異議を申し立てる（支援者のみ）</button>
        )}
        {status === "proposed" && now > challengeEnds && (
          <button disabled={props.busy} onClick={props.onFinalize}>結論を確定する</button>
        )}
        {status === "final" && grants && !c.released && (
          <button disabled={props.busy} onClick={props.onRelease}>受益者へ解放する</button>
        )}
        {status === "final" && !grants && (
          <button className="secondary" disabled={props.busy} onClick={props.onRefund}>自分の拠出分を返金する</button>
        )}
      </div>
    </article>
  );
}
