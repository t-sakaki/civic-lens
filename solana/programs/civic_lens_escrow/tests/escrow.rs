//! LiteSVM統合テスト。`anchor build`（または cargo-build-sbf）で生成した .so をロードして実行する。
//! クロックを直接進められるため、異議申立期間・返金猶予期間の境界も検証できる。

use anchor_lang::prelude::{Clock, Pubkey};
use anchor_lang::solana_program::instruction::Instruction;
use anchor_lang::{AccountDeserialize, InstructionData, ToAccountMetas};
use anchor_spl::associated_token::get_associated_token_address;
use civic_lens_escrow::{
    accounts, instruction, Case, CaseStatus, Outcome, ProfessionalKind, CASE_SEED, CHALLENGE_PERIOD_SECS,
    CONFIG_SEED, PLEDGE_SEED, PROFESSIONAL_SEED, REFUND_GRACE_SECS,
};
use litesvm::LiteSVM;
use litesvm_token::{CreateAssociatedTokenAccount, CreateMint, MintTo};
use solana_keypair::Keypair;
use solana_message::Message;
use solana_signer::Signer;
use solana_transaction::Transaction;

const SOL: u64 = 1_000_000_000;
const USDC: u64 = 1_000_000;
const DAY: i64 = 60 * 60 * 24;

struct Env {
    svm: LiteSVM,
    admin: Keypair,
    agent: Keypair,
    arbiter: Keypair,
    mint: Pubkey,
}

fn config_pda() -> Pubkey {
    Pubkey::find_program_address(&[CONFIG_SEED], &civic_lens_escrow::ID).0
}
fn case_pda(id: u64) -> Pubkey {
    Pubkey::find_program_address(&[CASE_SEED, &id.to_le_bytes()], &civic_lens_escrow::ID).0
}
fn pledge_pda(case: &Pubkey, supporter: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[PLEDGE_SEED, case.as_ref(), supporter.as_ref()], &civic_lens_escrow::ID).0
}
fn professional_pda(wallet: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[PROFESSIONAL_SEED, wallet.as_ref()], &civic_lens_escrow::ID).0
}

impl Env {
    fn new() -> Self {
        let mut svm = LiteSVM::new();
        svm.add_program_from_file(civic_lens_escrow::ID, "../../target/deploy/civic_lens_escrow.so")
            .expect("先に anchor build / cargo-build-sbf を実行してください");
        let (admin, agent, arbiter) = (Keypair::new(), Keypair::new(), Keypair::new());
        for k in [&admin, &agent, &arbiter] {
            svm.airdrop(&k.pubkey(), 100 * SOL).unwrap();
        }
        let mint = CreateMint::new(&mut svm, &admin).decimals(6).send().unwrap();
        let mut env = Env { svm, admin, agent, arbiter, mint };
        let ix = env.ix(
            accounts::Initialize {
                config: config_pda(),
                usdc_mint: env.mint,
                admin: env.admin.pubkey(),
                system_program: anchor_lang::system_program::ID,
            },
            instruction::Initialize { agent: env.agent.pubkey(), arbiter: env.arbiter.pubkey() },
        );
        let admin = env.admin.insecure_clone();
        env.send(ix, &[&admin]).unwrap();
        env
    }

    fn ix(&self, accs: impl ToAccountMetas, data: impl InstructionData) -> Instruction {
        Instruction { program_id: civic_lens_escrow::ID, accounts: accs.to_account_metas(None), data: data.data() }
    }

    fn send(&mut self, ix: Instruction, signers: &[&Keypair]) -> Result<(), String> {
        let msg = Message::new(&[ix], Some(&signers[0].pubkey()));
        let tx = Transaction::new(signers, msg, self.svm.latest_blockhash());
        let res = self.svm.send_transaction(tx).map(|_| ()).map_err(|e| format!("{:?}", e.err));
        self.svm.expire_blockhash();
        res
    }

    fn user(&mut self) -> Keypair {
        let k = Keypair::new();
        self.svm.airdrop(&k.pubkey(), 100 * SOL).unwrap();
        k
    }

    fn usdc_account(&mut self, owner: &Keypair, amount: u64) -> Pubkey {
        let admin = self.admin.insecure_clone();
        let mint = self.mint;
        let ata = CreateAssociatedTokenAccount::new(&mut self.svm, &admin, &mint).owner(&owner.pubkey()).send().unwrap();
        if amount > 0 {
            MintTo::new(&mut self.svm, &admin, &mint, &ata, amount).send().unwrap();
        }
        ata
    }

    fn usdc_balance(&self, ata: &Pubkey) -> u64 {
        let acc = self.svm.get_account(ata).unwrap();
        u64::from_le_bytes(acc.data[64..72].try_into().unwrap())
    }

    fn case(&self, id: u64) -> Case {
        let acc = self.svm.get_account(&case_pda(id)).unwrap();
        Case::try_deserialize(&mut acc.data.as_slice()).unwrap()
    }

    fn now(&self) -> i64 {
        self.svm.get_sysvar::<Clock>().unix_timestamp
    }

    fn warp(&mut self, secs: i64) {
        let mut clock: Clock = self.svm.get_sysvar();
        clock.unix_timestamp += secs;
        clock.slot += 1;
        self.svm.set_sysvar(&clock);
    }

    fn create_case(&mut self, creator: &Keypair, beneficiary: Pubkey, with_professional: bool) -> Result<u64, String> {
        let id = {
            let acc = self.svm.get_account(&config_pda()).unwrap();
            civic_lens_escrow::Config::try_deserialize(&mut acc.data.as_slice()).unwrap().case_count
        };
        let case = case_pda(id);
        let deadline = self.now() + 30 * DAY;
        let ix = self.ix(
            accounts::CreateCase {
                config: config_pda(),
                case,
                case_vault: get_associated_token_address(&case, &self.mint),
                usdc_mint: self.mint,
                professional: with_professional.then(|| professional_pda(&beneficiary)),
                creator: creator.pubkey(),
                token_program: anchor_spl::token::ID,
                associated_token_program: anchor_spl::associated_token::ID,
                system_program: anchor_lang::system_program::ID,
            },
            instruction::CreateCase {
                title: "生活保護費の減額決定に対する審査請求".to_string(),
                evidence_hash: [7u8; 32],
                evidence_uri: "ipfs://bafy.../evidence.pdf".to_string(),
                review_key_hash: [9u8; 32],
                beneficiary,
                deadline,
            },
        );
        self.send(ix, &[creator]).map(|_| id)
    }

    fn pledge_sol(&mut self, id: u64, supporter: &Keypair, amount: u64) -> Result<(), String> {
        let case = case_pda(id);
        let ix = self.ix(
            accounts::PledgeSol {
                case,
                pledge: pledge_pda(&case, &supporter.pubkey()),
                supporter: supporter.pubkey(),
                system_program: anchor_lang::system_program::ID,
            },
            instruction::PledgeSol { amount },
        );
        self.send(ix, &[supporter])
    }

    fn pledge_usdc(&mut self, id: u64, supporter: &Keypair, supporter_ata: Pubkey, amount: u64) -> Result<(), String> {
        let case = case_pda(id);
        let ix = self.ix(
            accounts::PledgeUsdc {
                config: config_pda(),
                case,
                pledge: pledge_pda(&case, &supporter.pubkey()),
                case_vault: get_associated_token_address(&case, &self.mint),
                supporter_ata,
                usdc_mint: self.mint,
                supporter: supporter.pubkey(),
                token_program: anchor_spl::token::ID,
                system_program: anchor_lang::system_program::ID,
            },
            instruction::PledgeUsdc { amount },
        );
        self.send(ix, &[supporter])
    }

    fn propose(&mut self, id: u64, signer: &Keypair, outcome: Outcome) -> Result<(), String> {
        let ix = self.ix(
            accounts::ProposeResolution { config: config_pda(), case: case_pda(id), agent: signer.pubkey() },
            instruction::ProposeResolution {
                outcome,
                source_hash: [1u8; 32],
                source_uri: "fufukudb:厚生労働省/2026-08-01/第123号".to_string(),
            },
        );
        self.send(ix, &[signer])
    }

    fn challenge(&mut self, id: u64, supporter: &Keypair) -> Result<(), String> {
        let case = case_pda(id);
        let ix = self.ix(
            accounts::Challenge { case, pledge: pledge_pda(&case, &supporter.pubkey()), supporter: supporter.pubkey() },
            instruction::Challenge {},
        );
        self.send(ix, &[supporter])
    }

    fn finalize(&mut self, id: u64, payer: &Keypair) -> Result<(), String> {
        let ix = self.ix(accounts::Finalize { case: case_pda(id) }, instruction::Finalize {});
        self.send(ix, &[payer])
    }

    fn arbitrate(&mut self, id: u64, signer: &Keypair, outcome: Outcome) -> Result<(), String> {
        let ix = self.ix(
            accounts::Arbitrate { config: config_pda(), case: case_pda(id), arbiter: signer.pubkey() },
            instruction::Arbitrate { outcome, source_hash: [2u8; 32] },
        );
        self.send(ix, &[signer])
    }

    fn release(&mut self, id: u64, beneficiary: Pubkey, payer: &Keypair) -> Result<(), String> {
        let case = case_pda(id);
        let ix = self.ix(
            accounts::Release {
                config: config_pda(),
                case,
                beneficiary,
                case_vault: get_associated_token_address(&case, &self.mint),
                beneficiary_ata: get_associated_token_address(&beneficiary, &self.mint),
                usdc_mint: self.mint,
                payer: payer.pubkey(),
                token_program: anchor_spl::token::ID,
                associated_token_program: anchor_spl::associated_token::ID,
                system_program: anchor_lang::system_program::ID,
            },
            instruction::Release {},
        );
        self.send(ix, &[payer])
    }

    fn refund(&mut self, id: u64, supporter: &Keypair) -> Result<(), String> {
        let case = case_pda(id);
        let ix = self.ix(
            accounts::Refund {
                config: config_pda(),
                case,
                pledge: pledge_pda(&case, &supporter.pubkey()),
                case_vault: get_associated_token_address(&case, &self.mint),
                supporter_ata: get_associated_token_address(&supporter.pubkey(), &self.mint),
                usdc_mint: self.mint,
                supporter: supporter.pubkey(),
                token_program: anchor_spl::token::ID,
                associated_token_program: anchor_spl::associated_token::ID,
                system_program: anchor_lang::system_program::ID,
            },
            instruction::Refund {},
        );
        self.send(ix, &[supporter])
    }

    fn register_professional(&mut self, wallet: Pubkey) {
        let ix = self.ix(
            accounts::RegisterProfessional {
                config: config_pda(),
                professional: professional_pda(&wallet),
                admin: self.admin.pubkey(),
                system_program: anchor_lang::system_program::ID,
            },
            instruction::RegisterProfessional { wallet, kind: ProfessionalKind::Lawyer, registration_hash: [3u8; 32] },
        );
        let admin = self.admin.insecure_clone();
        self.send(ix, &[&admin]).unwrap();
    }

    fn set_professional_active(&mut self, wallet: Pubkey, active: bool) {
        let ix = self.ix(
            accounts::SetProfessionalActive {
                config: config_pda(),
                professional: professional_pda(&wallet),
                admin: self.admin.pubkey(),
            },
            instruction::SetProfessionalActive { active },
        );
        let admin = self.admin.insecure_clone();
        self.send(ix, &[&admin]).unwrap();
    }
}

#[test]
fn granted_case_releases_funds_to_beneficiary_after_challenge_period() {
    let mut env = Env::new();
    let creator = env.user();
    let id = env.create_case(&creator, creator.pubkey(), false).unwrap();

    let (alice, bob) = (env.user(), env.user());
    let alice_ata = env.usdc_account(&alice, 50 * USDC);
    env.pledge_sol(id, &alice, 2 * SOL).unwrap();
    env.pledge_sol(id, &alice, SOL).unwrap(); // 同一支援者の追加拠出
    env.pledge_usdc(id, &alice, alice_ata, 20 * USDC).unwrap();
    env.pledge_sol(id, &bob, SOL).unwrap();

    let case = env.case(id);
    assert_eq!((case.total_sol, case.total_usdc, case.supporter_count), (4 * SOL, 20 * USDC, 2));

    // エージェント以外は提案できない
    assert!(env.propose(id, &bob, Outcome::Granted).is_err());
    let agent = env.agent.insecure_clone();
    env.propose(id, &agent, Outcome::Granted).unwrap();
    // 提案後は支援受付終了・再提案不可
    assert!(env.pledge_sol(id, &bob, SOL).is_err());
    assert!(env.propose(id, &agent, Outcome::Dismissed).is_err());

    // 異議申立期間中は確定・解放できない
    assert!(env.finalize(id, &bob).is_err());
    assert!(env.release(id, creator.pubkey(), &bob).is_err());

    env.warp(CHALLENGE_PERIOD_SECS + 1);
    assert!(env.challenge(id, &alice).is_err(), "期間経過後の異議は不可");
    env.finalize(id, &bob).unwrap(); // 誰でもcrank可
    assert_eq!(env.case(id).status, CaseStatus::Final);

    // 認容確定後は返金不可
    assert!(env.refund(id, &alice).is_err());

    let before = env.svm.get_balance(&creator.pubkey()).unwrap();
    env.release(id, creator.pubkey(), &bob).unwrap();
    assert_eq!(env.svm.get_balance(&creator.pubkey()).unwrap() - before, 4 * SOL);
    let creator_ata = get_associated_token_address(&creator.pubkey(), &env.mint);
    assert_eq!(env.usdc_balance(&creator_ata), 20 * USDC);

    // 二重解放不可
    assert!(env.release(id, creator.pubkey(), &bob).is_err());
    // 受益者以外への解放は不可（has_one）
    let mallory = env.user();
    assert!(env.release(id, mallory.pubkey(), &mallory).is_err());
}

#[test]
fn dismissed_case_refunds_each_supporter_once() {
    let mut env = Env::new();
    let creator = env.user();
    let id = env.create_case(&creator, creator.pubkey(), false).unwrap();
    let alice = env.user();
    let alice_ata = env.usdc_account(&alice, 50 * USDC);
    env.pledge_sol(id, &alice, 3 * SOL).unwrap();
    env.pledge_usdc(id, &alice, alice_ata, 15 * USDC).unwrap();

    let agent = env.agent.insecure_clone();
    env.propose(id, &agent, Outcome::Dismissed).unwrap();
    env.warp(CHALLENGE_PERIOD_SECS + 1);
    env.finalize(id, &alice).unwrap();

    assert!(env.release(id, creator.pubkey(), &alice).is_err(), "棄却では解放不可");

    let before = env.svm.get_balance(&alice.pubkey()).unwrap();
    env.refund(id, &alice).unwrap();
    // 拠出SOL + pledgeアカウントのrent返還分だけ増える（手数料は差し引かれる）
    assert!(env.svm.get_balance(&alice.pubkey()).unwrap() > before + 3 * SOL - 10_000);
    assert_eq!(env.usdc_balance(&alice_ata), 50 * USDC);
    let case = env.case(id);
    assert_eq!((case.total_sol, case.total_usdc), (0, 0));

    assert!(env.refund(id, &alice).is_err(), "二重返金不可（pledgeはclose済み）");
}

#[test]
fn challenged_resolution_goes_to_arbiter() {
    let mut env = Env::new();
    let creator = env.user();
    let id = env.create_case(&creator, creator.pubkey(), false).unwrap();
    let (alice, outsider) = (env.user(), env.user());
    env.pledge_sol(id, &alice, SOL).unwrap();

    let agent = env.agent.insecure_clone();
    env.propose(id, &agent, Outcome::Granted).unwrap();
    assert!(env.challenge(id, &outsider).is_err(), "支援していない者は異議不可");
    env.challenge(id, &alice).unwrap();

    env.warp(CHALLENGE_PERIOD_SECS + 1);
    assert!(env.finalize(id, &alice).is_err(), "異議中は自動確定しない");
    assert!(env.arbitrate(id, &agent, Outcome::Granted).is_err(), "arbiter以外は裁定不可");

    let arbiter = env.arbiter.insecure_clone();
    env.arbitrate(id, &arbiter, Outcome::Rejected).unwrap();
    let case = env.case(id);
    assert_eq!((case.status, case.outcome), (CaseStatus::Final, Some(Outcome::Rejected)));
    env.refund(id, &alice).unwrap();
}

#[test]
fn partially_granted_releases_funds() {
    let mut env = Env::new();
    let creator = env.user();
    let id = env.create_case(&creator, creator.pubkey(), false).unwrap();
    let alice = env.user();
    env.pledge_sol(id, &alice, SOL).unwrap();
    let agent = env.agent.insecure_clone();
    env.propose(id, &agent, Outcome::PartiallyGranted).unwrap();
    env.warp(CHALLENGE_PERIOD_SECS + 1);
    env.finalize(id, &alice).unwrap();
    env.release(id, creator.pubkey(), &alice).unwrap();
}

#[test]
fn stale_case_becomes_refundable_and_rejects_late_resolution() {
    let mut env = Env::new();
    let creator = env.user();
    let id = env.create_case(&creator, creator.pubkey(), false).unwrap();
    let alice = env.user();
    env.pledge_sol(id, &alice, SOL).unwrap();

    env.warp(31 * DAY);
    assert!(env.pledge_sol(id, &alice, SOL).is_err(), "受付期限後は拠出不可");
    assert!(env.refund(id, &alice).is_err(), "猶予期間中は返金不可");

    env.warp(REFUND_GRACE_SECS);
    let agent = env.agent.insecure_clone();
    assert!(env.propose(id, &agent, Outcome::Granted).is_err(), "猶予期間経過後は結論を受け付けない");
    env.refund(id, &alice).unwrap();
}

#[test]
fn beneficiary_must_be_creator_or_registered_professional() {
    let mut env = Env::new();
    let creator = env.user();
    let lawyer = env.user();
    let unlicensed = env.user();

    // 無資格の第三者を受益者にはできない（弁護士法72条対策）
    assert!(env.create_case(&creator, unlicensed.pubkey(), false).is_err());

    env.register_professional(lawyer.pubkey());
    // 他人の登録を流用しても seeds 不一致で拒否される
    let ix_err = {
        let id_before = env.create_case(&creator, unlicensed.pubkey(), true);
        id_before.is_err()
    };
    assert!(ix_err);

    let id = env.create_case(&creator, lawyer.pubkey(), true).unwrap();
    assert_eq!(env.case(id).beneficiary, lawyer.pubkey());

    env.set_professional_active(lawyer.pubkey(), false);
    assert!(env.create_case(&creator, lawyer.pubkey(), true).is_err(), "登録無効化後は受益者にできない");
}
