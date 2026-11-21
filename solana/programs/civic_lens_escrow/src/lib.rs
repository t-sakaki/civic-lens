//! Civic Lens — 行政不服審査ケースの支援金エスクロー
//!
//! 市民が提起した不服審査ケースに対し、支援者がSOL/USDCを預託する。
//! 結論の確定は「総務省 行政不服審査裁決・答申検索データベース」をオラクルとし、
//! オフチェーンの ResolutionAgent（Gemini）が掲載裁決を突合して `propose_resolution` を提出する。
//! 異議申立期間内に支援者が `challenge` しなければ誰でも `finalize` でき、
//! 異議があった場合のみ arbiter（マルチシグ想定）が `arbitrate` で最終判断する。
//!
//! 弁護士法72条（非弁行為の禁止）への配慮:
//!   - 受益者は「申立人本人」または「adminが登録を確認した有資格者（弁護士等）」に限定する。
//!     本人の事件の実費を本人が受け取ることや、有資格者が報酬を受けることは72条の対象外。
//!     無資格の第三者が報酬を受け取って代理・書面作成を行う構造はプログラム上作れない。
//!   - 支援者への金銭的リターンは一切なし（寄附）。結論に連動して返るのは各自の元本の返金のみ。
//!   - 運営は資金の送り先を任意に変更できない（arbiterも結論を選べるだけ）。
//!
//! 資金の流れ:
//!   - 認容・一部認容 → 受益者へ解放 (`release`)
//!   - 棄却・却下・取下げ、または長期未確定 → 支援者へ各自の拠出分を全額返金 (`refund`)

use anchor_lang::prelude::*;
use anchor_lang::system_program;
use anchor_spl::associated_token::AssociatedToken;
use anchor_spl::token_interface::{self, Mint, TokenAccount, TokenInterface, TransferChecked};

declare_id!("Ca7tu8tfHfJt4TgEzcPxKWtYiYUCigvWh6tr78Q7TXQ4");

/// UTF-8で日本語約40文字
pub const MAX_TITLE: usize = 128;
pub const MAX_URI: usize = 200;
/// 結論提案から確定までの異議申立期間
pub const CHALLENGE_PERIOD_SECS: i64 = 60 * 60 * 24 * 7;
/// 支援受付期限後、この期間を過ぎても結論が確定しなければ返金可能（審理+DB掲載のラグを考慮）
pub const REFUND_GRACE_SECS: i64 = 60 * 60 * 24 * 540;

pub const CONFIG_SEED: &[u8] = b"config";
pub const CASE_SEED: &[u8] = b"case";
pub const PLEDGE_SEED: &[u8] = b"pledge";
pub const PROFESSIONAL_SEED: &[u8] = b"professional";

#[program]
pub mod civic_lens_escrow {
    use super::*;

    pub fn initialize(ctx: Context<Initialize>, agent: Pubkey, arbiter: Pubkey) -> Result<()> {
        let cfg = &mut ctx.accounts.config;
        cfg.admin = ctx.accounts.admin.key();
        cfg.agent = agent;
        cfg.arbiter = arbiter;
        cfg.usdc_mint = ctx.accounts.usdc_mint.key();
        cfg.case_count = 0;
        cfg.bump = ctx.bumps.config;
        Ok(())
    }

    /// エージェント鍵・arbiterのローテーション（adminのみ）
    pub fn set_roles(ctx: Context<SetRoles>, agent: Pubkey, arbiter: Pubkey) -> Result<()> {
        let cfg = &mut ctx.accounts.config;
        cfg.agent = agent;
        cfg.arbiter = arbiter;
        Ok(())
    }

    /// 有資格者の登録（adminが弁護士会等の登録をオフチェーンで確認した上で行う）。
    /// 登録番号そのものは載せず、ハッシュのみ記録する。
    pub fn register_professional(
        ctx: Context<RegisterProfessional>,
        wallet: Pubkey,
        kind: ProfessionalKind,
        registration_hash: [u8; 32],
    ) -> Result<()> {
        let pro = &mut ctx.accounts.professional;
        pro.wallet = wallet;
        pro.kind = kind;
        pro.registration_hash = registration_hash;
        pro.active = true;
        pro.bump = ctx.bumps.professional;
        Ok(())
    }

    /// 登録抹消・懲戒等があった場合に無効化（以後そのウォレットを受益者とするケースは作れない）
    pub fn set_professional_active(ctx: Context<SetProfessionalActive>, active: bool) -> Result<()> {
        ctx.accounts.professional.active = active;
        Ok(())
    }

    /// `beneficiary` は申立人本人（= creator）か、有効な `Professional` 登録のあるウォレットのみ。
    /// `review_key_hash`: sha256(審査庁 | 処分の種類 | 申立日) — DB掲載裁決との突合キー。
    /// 個人情報はオンチェーンに載せず、証拠もハッシュ＋削除可能なストレージのURIのみ。
    pub fn create_case(
        ctx: Context<CreateCase>,
        title: String,
        evidence_hash: [u8; 32],
        evidence_uri: String,
        review_key_hash: [u8; 32],
        beneficiary: Pubkey,
        deadline: i64,
    ) -> Result<()> {
        require!(title.len() <= MAX_TITLE, CivicError::TitleTooLong);
        require!(evidence_uri.len() <= MAX_URI, CivicError::UriTooLong);
        require!(deadline > Clock::get()?.unix_timestamp, CivicError::InvalidDeadline);
        if beneficiary != ctx.accounts.creator.key() {
            // seeds制約により professional.wallet == beneficiary は保証済み
            let pro = ctx.accounts.professional.as_ref().ok_or(CivicError::BeneficiaryNotAllowed)?;
            require!(pro.active, CivicError::BeneficiaryNotAllowed);
        }

        let cfg = &mut ctx.accounts.config;
        let case = &mut ctx.accounts.case;
        case.id = cfg.case_count;
        case.creator = ctx.accounts.creator.key();
        case.beneficiary = beneficiary;
        case.title = title;
        case.evidence_hash = evidence_hash;
        case.evidence_uri = evidence_uri;
        case.review_key_hash = review_key_hash;
        case.status = CaseStatus::UnderReview;
        case.outcome = None;
        case.deadline = deadline;
        case.bump = ctx.bumps.case;
        cfg.case_count = cfg.case_count.checked_add(1).ok_or(CivicError::Overflow)?;

        emit!(CaseCreated { case: case.key(), id: case.id, creator: case.creator, beneficiary });
        Ok(())
    }

    pub fn pledge_sol(ctx: Context<PledgeSol>, amount: u64) -> Result<()> {
        require!(amount > 0, CivicError::ZeroAmount);
        ctx.accounts.case.assert_open()?;
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.key(),
                system_program::Transfer {
                    from: ctx.accounts.supporter.to_account_info(),
                    to: ctx.accounts.case.to_account_info(),
                },
            ),
            amount,
        )?;
        let supporter = ctx.accounts.supporter.key();
        let pledge = &mut ctx.accounts.pledge;
        let case = &mut ctx.accounts.case;
        pledge.init_if_new(case, supporter, ctx.bumps.pledge)?;
        pledge.sol_amount = pledge.sol_amount.checked_add(amount).ok_or(CivicError::Overflow)?;
        case.total_sol = case.total_sol.checked_add(amount).ok_or(CivicError::Overflow)?;

        emit!(Pledged { case: case.key(), supporter, sol_amount: amount, usdc_amount: 0 });
        Ok(())
    }

    pub fn pledge_usdc(ctx: Context<PledgeUsdc>, amount: u64) -> Result<()> {
        require!(amount > 0, CivicError::ZeroAmount);
        ctx.accounts.case.assert_open()?;
        token_interface::transfer_checked(
            CpiContext::new(
                ctx.accounts.token_program.key(),
                TransferChecked {
                    from: ctx.accounts.supporter_ata.to_account_info(),
                    mint: ctx.accounts.usdc_mint.to_account_info(),
                    to: ctx.accounts.case_vault.to_account_info(),
                    authority: ctx.accounts.supporter.to_account_info(),
                },
            ),
            amount,
            ctx.accounts.usdc_mint.decimals,
        )?;
        let supporter = ctx.accounts.supporter.key();
        let pledge = &mut ctx.accounts.pledge;
        let case = &mut ctx.accounts.case;
        pledge.init_if_new(case, supporter, ctx.bumps.pledge)?;
        pledge.usdc_amount = pledge.usdc_amount.checked_add(amount).ok_or(CivicError::Overflow)?;
        case.total_usdc = case.total_usdc.checked_add(amount).ok_or(CivicError::Overflow)?;

        emit!(Pledged { case: case.key(), supporter, sol_amount: 0, usdc_amount: amount });
        Ok(())
    }

    /// ResolutionAgent（config.agent）のみ。DB掲載裁決の本文ハッシュとURLを根拠として記録する。
    pub fn propose_resolution(
        ctx: Context<ProposeResolution>,
        outcome: Outcome,
        source_hash: [u8; 32],
        source_uri: String,
    ) -> Result<()> {
        require!(source_uri.len() <= MAX_URI, CivicError::UriTooLong);
        let now = Clock::get()?.unix_timestamp;
        let case = &mut ctx.accounts.case;
        require!(case.status == CaseStatus::UnderReview, CivicError::AlreadyResolved);
        // 返金が始まりうる期間に入った後は結論を受け付けない（会計の一貫性のため）
        require!(!case.refund_window_open(now), CivicError::ResolutionExpired);
        case.outcome = Some(outcome);
        case.source_hash = source_hash;
        case.source_uri = source_uri;
        case.proposed_at = now;
        case.status = CaseStatus::Proposed;

        emit!(ResolutionProposed { case: case.key(), outcome, source_hash });
        Ok(())
    }

    /// 支援者（Pledge保有者）のみ。異議申立期間内に限る。
    pub fn challenge(ctx: Context<Challenge>) -> Result<()> {
        let case = &mut ctx.accounts.case;
        require!(case.status == CaseStatus::Proposed, CivicError::NotProposed);
        require!(
            Clock::get()?.unix_timestamp <= case.challenge_deadline(),
            CivicError::ChallengeClosed
        );
        case.status = CaseStatus::Challenged;

        emit!(ResolutionChallenged { case: case.key(), challenger: ctx.accounts.supporter.key() });
        Ok(())
    }

    /// 異議なしで期間経過 → 誰でも確定可（パーミッションレスなcrank）
    pub fn finalize(ctx: Context<Finalize>) -> Result<()> {
        let case = &mut ctx.accounts.case;
        require!(case.status == CaseStatus::Proposed, CivicError::NotProposed);
        require!(
            Clock::get()?.unix_timestamp > case.challenge_deadline(),
            CivicError::ChallengeOpen
        );
        case.status = CaseStatus::Final;

        emit!(ResolutionFinalized { case: case.key(), outcome: case.outcome.unwrap_or(Outcome::Withdrawn) });
        Ok(())
    }

    /// 異議時のみ arbiter が最終結論を決定する
    pub fn arbitrate(ctx: Context<Arbitrate>, outcome: Outcome, source_hash: [u8; 32]) -> Result<()> {
        let case = &mut ctx.accounts.case;
        require!(case.status == CaseStatus::Challenged, CivicError::NotChallenged);
        case.outcome = Some(outcome);
        case.source_hash = source_hash;
        case.status = CaseStatus::Final;

        emit!(ResolutionFinalized { case: case.key(), outcome });
        Ok(())
    }

    /// 認容・一部認容で確定 → 預託金を受益者へ解放。誰でもcrank可。
    pub fn release(ctx: Context<Release>) -> Result<()> {
        let case = &ctx.accounts.case;
        require!(case.status == CaseStatus::Final, CivicError::NotFinal);
        require!(case.outcome.is_some_and(|o| o.releases_funds()), CivicError::NotGranted);
        require!(!case.released, CivicError::AlreadyReleased);
        let (sol, usdc, id, bump) = (case.total_sol, case.total_usdc, case.id.to_le_bytes(), case.bump);

        if usdc > 0 {
            let seeds: &[&[&[u8]]] = &[&[CASE_SEED, &id, &[bump]]];
            token_interface::transfer_checked(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.key(),
                    TransferChecked {
                        from: ctx.accounts.case_vault.to_account_info(),
                        mint: ctx.accounts.usdc_mint.to_account_info(),
                        to: ctx.accounts.beneficiary_ata.to_account_info(),
                        authority: ctx.accounts.case.to_account_info(),
                    },
                    seeds,
                ),
                usdc,
                ctx.accounts.usdc_mint.decimals,
            )?;
        }
        if sol > 0 {
            ctx.accounts.case.sub_lamports(sol)?;
            ctx.accounts.beneficiary.add_lamports(sol)?;
        }
        ctx.accounts.case.released = true;

        emit!(FundsReleased { case: ctx.accounts.case.key(), beneficiary: ctx.accounts.beneficiary.key(), sol, usdc });
        Ok(())
    }

    /// 棄却・却下・取下げで確定、または期限+猶予を過ぎても未確定 → 支援者が自分の拠出分を回収。
    /// Pledgeアカウントは close = supporter で閉じるため二重返金は起きない。
    pub fn refund(ctx: Context<Refund>) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        let case = &ctx.accounts.case;
        let lost = case.status == CaseStatus::Final
            && case.outcome.is_some_and(|o| !o.releases_funds());
        let stale = case.status != CaseStatus::Final && case.refund_window_open(now);
        require!(lost || stale, CivicError::RefundNotAvailable);

        let (sol, usdc) = (ctx.accounts.pledge.sol_amount, ctx.accounts.pledge.usdc_amount);
        let (id, bump) = (case.id.to_le_bytes(), case.bump);

        if usdc > 0 {
            let seeds: &[&[&[u8]]] = &[&[CASE_SEED, &id, &[bump]]];
            token_interface::transfer_checked(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.key(),
                    TransferChecked {
                        from: ctx.accounts.case_vault.to_account_info(),
                        mint: ctx.accounts.usdc_mint.to_account_info(),
                        to: ctx.accounts.supporter_ata.to_account_info(),
                        authority: ctx.accounts.case.to_account_info(),
                    },
                    seeds,
                ),
                usdc,
                ctx.accounts.usdc_mint.decimals,
            )?;
        }
        if sol > 0 {
            ctx.accounts.case.sub_lamports(sol)?;
            ctx.accounts.supporter.add_lamports(sol)?;
        }
        let case = &mut ctx.accounts.case;
        case.total_sol = case.total_sol.checked_sub(sol).ok_or(CivicError::Overflow)?;
        case.total_usdc = case.total_usdc.checked_sub(usdc).ok_or(CivicError::Overflow)?;

        emit!(Refunded { case: case.key(), supporter: ctx.accounts.supporter.key(), sol, usdc });
        Ok(())
    }
}

// ───────── State ─────────

#[account]
#[derive(InitSpace)]
pub struct Config {
    pub admin: Pubkey,
    /// ResolutionAgent（総務省DBを参照するオフチェーンエージェント）の署名鍵
    pub agent: Pubkey,
    /// 異議時の最終判断者（Squadsマルチシグ想定）
    pub arbiter: Pubkey,
    pub usdc_mint: Pubkey,
    pub case_count: u64,
    pub bump: u8,
}

/// 不服申立ての代理が法律上認められる資格の種別（業務範囲の確認はオフチェーンで行う）
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, Debug, InitSpace)]
pub enum ProfessionalKind {
    /// 弁護士・弁護士法人
    Lawyer,
    /// 特定行政書士（行政書士が作成した許認可申請に係る審査請求の代理）
    SpecifiedGyoseishoshi,
    /// 社会保険労務士（社会保険・労働保険に関する審査請求の代理）
    SharoushiLaborInsurance,
    /// 税理士（税務に関する不服申立ての代理）
    TaxAccountant,
}

#[account]
#[derive(InitSpace)]
pub struct Professional {
    pub wallet: Pubkey,
    pub kind: ProfessionalKind,
    /// sha256(登録番号) — 登録番号そのものはオンチェーンに載せない
    pub registration_hash: [u8; 32],
    pub active: bool,
    pub bump: u8,
}

/// 行政不服審査の結論区分
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, Debug, InitSpace)]
pub enum Outcome {
    /// 認容
    Granted,
    /// 一部認容（実費は発生済みのため全額解放）
    PartiallyGranted,
    /// 棄却（本案審理の上で請求を退ける）
    Dismissed,
    /// 却下（要件不備による門前払い）
    Rejected,
    /// 取下げ
    Withdrawn,
}

impl Outcome {
    pub fn releases_funds(self) -> bool {
        matches!(self, Outcome::Granted | Outcome::PartiallyGranted)
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, Debug, InitSpace)]
pub enum CaseStatus {
    UnderReview,
    Proposed,
    Challenged,
    Final,
}

#[account]
#[derive(InitSpace)]
pub struct Case {
    pub id: u64,
    pub creator: Pubkey,
    pub beneficiary: Pubkey,
    #[max_len(MAX_TITLE)]
    pub title: String,
    pub evidence_hash: [u8; 32],
    #[max_len(MAX_URI)]
    pub evidence_uri: String,
    pub review_key_hash: [u8; 32],
    pub status: CaseStatus,
    pub outcome: Option<Outcome>,
    /// DB掲載裁決本文のsha256
    pub source_hash: [u8; 32],
    /// 総務省DBの該当URL
    #[max_len(MAX_URI)]
    pub source_uri: String,
    pub proposed_at: i64,
    pub total_sol: u64,
    pub total_usdc: u64,
    pub supporter_count: u32,
    pub deadline: i64,
    pub released: bool,
    pub bump: u8,
}

impl Case {
    fn assert_open(&self) -> Result<()> {
        require!(self.status == CaseStatus::UnderReview, CivicError::CaseClosed);
        require!(Clock::get()?.unix_timestamp <= self.deadline, CivicError::CaseClosed);
        Ok(())
    }

    fn challenge_deadline(&self) -> i64 {
        self.proposed_at.saturating_add(CHALLENGE_PERIOD_SECS)
    }

    fn refund_window_open(&self, now: i64) -> bool {
        now > self.deadline.saturating_add(REFUND_GRACE_SECS)
    }
}

#[account]
#[derive(InitSpace)]
pub struct Pledge {
    pub case: Pubkey,
    pub supporter: Pubkey,
    pub sol_amount: u64,
    pub usdc_amount: u64,
    pub bump: u8,
}

impl Pledge {
    fn init_if_new(&mut self, case: &mut Account<Case>, supporter: Pubkey, bump: u8) -> Result<()> {
        if self.supporter == Pubkey::default() {
            self.case = case.key();
            self.supporter = supporter;
            self.bump = bump;
            case.supporter_count = case.supporter_count.checked_add(1).ok_or(CivicError::Overflow)?;
        }
        Ok(())
    }
}

// ───────── Events ─────────

#[event]
pub struct CaseCreated {
    pub case: Pubkey,
    pub id: u64,
    pub creator: Pubkey,
    pub beneficiary: Pubkey,
}

#[event]
pub struct Pledged {
    pub case: Pubkey,
    pub supporter: Pubkey,
    pub sol_amount: u64,
    pub usdc_amount: u64,
}

#[event]
pub struct ResolutionProposed {
    pub case: Pubkey,
    pub outcome: Outcome,
    pub source_hash: [u8; 32],
}

#[event]
pub struct ResolutionChallenged {
    pub case: Pubkey,
    pub challenger: Pubkey,
}

#[event]
pub struct ResolutionFinalized {
    pub case: Pubkey,
    pub outcome: Outcome,
}

#[event]
pub struct FundsReleased {
    pub case: Pubkey,
    pub beneficiary: Pubkey,
    pub sol: u64,
    pub usdc: u64,
}

#[event]
pub struct Refunded {
    pub case: Pubkey,
    pub supporter: Pubkey,
    pub sol: u64,
    pub usdc: u64,
}

// ───────── Accounts ─────────

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(init, payer = admin, space = 8 + Config::INIT_SPACE, seeds = [CONFIG_SEED], bump)]
    pub config: Account<'info, Config>,
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SetRoles<'info> {
    #[account(mut, seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(wallet: Pubkey)]
pub struct RegisterProfessional<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = admin,
        space = 8 + Professional::INIT_SPACE,
        seeds = [PROFESSIONAL_SEED, wallet.as_ref()],
        bump
    )]
    pub professional: Account<'info, Professional>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SetProfessionalActive<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    #[account(mut, seeds = [PROFESSIONAL_SEED, professional.wallet.as_ref()], bump = professional.bump)]
    pub professional: Account<'info, Professional>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(
    title: String,
    evidence_hash: [u8; 32],
    evidence_uri: String,
    review_key_hash: [u8; 32],
    beneficiary: Pubkey
)]
pub struct CreateCase<'info> {
    #[account(mut, seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = creator,
        space = 8 + Case::INIT_SPACE,
        seeds = [CASE_SEED, config.case_count.to_le_bytes().as_ref()],
        bump
    )]
    pub case: Account<'info, Case>,
    #[account(
        init,
        payer = creator,
        associated_token::mint = usdc_mint,
        associated_token::authority = case,
        associated_token::token_program = token_program
    )]
    pub case_vault: InterfaceAccount<'info, TokenAccount>,
    #[account(address = config.usdc_mint)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    /// beneficiary が creator 本人でない場合のみ必須
    #[account(seeds = [PROFESSIONAL_SEED, beneficiary.as_ref()], bump = professional.bump)]
    pub professional: Option<Account<'info, Professional>>,
    #[account(mut)]
    pub creator: Signer<'info>,
    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct PledgeSol<'info> {
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    #[account(
        init_if_needed,
        payer = supporter,
        space = 8 + Pledge::INIT_SPACE,
        seeds = [PLEDGE_SEED, case.key().as_ref(), supporter.key().as_ref()],
        bump
    )]
    pub pledge: Account<'info, Pledge>,
    #[account(mut)]
    pub supporter: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct PledgeUsdc<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    #[account(
        init_if_needed,
        payer = supporter,
        space = 8 + Pledge::INIT_SPACE,
        seeds = [PLEDGE_SEED, case.key().as_ref(), supporter.key().as_ref()],
        bump
    )]
    pub pledge: Account<'info, Pledge>,
    #[account(
        mut,
        associated_token::mint = usdc_mint,
        associated_token::authority = case,
        associated_token::token_program = token_program
    )]
    pub case_vault: InterfaceAccount<'info, TokenAccount>,
    #[account(mut, token::mint = usdc_mint, token::authority = supporter, token::token_program = token_program)]
    pub supporter_ata: InterfaceAccount<'info, TokenAccount>,
    #[account(address = config.usdc_mint)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    #[account(mut)]
    pub supporter: Signer<'info>,
    pub token_program: Interface<'info, TokenInterface>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ProposeResolution<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = agent)]
    pub config: Account<'info, Config>,
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    pub agent: Signer<'info>,
}

#[derive(Accounts)]
pub struct Challenge<'info> {
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    #[account(
        seeds = [PLEDGE_SEED, case.key().as_ref(), supporter.key().as_ref()],
        bump = pledge.bump,
        has_one = case,
        has_one = supporter
    )]
    pub pledge: Account<'info, Pledge>,
    pub supporter: Signer<'info>,
}

#[derive(Accounts)]
pub struct Finalize<'info> {
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
}

#[derive(Accounts)]
pub struct Arbitrate<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = arbiter)]
    pub config: Account<'info, Config>,
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    pub arbiter: Signer<'info>,
}

#[derive(Accounts)]
pub struct Release<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        mut,
        seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()],
        bump = case.bump,
        has_one = beneficiary
    )]
    pub case: Account<'info, Case>,
    /// CHECK: case.beneficiary と has_one で一致を検証済み。lamportsの受取先としてのみ使う。
    #[account(mut)]
    pub beneficiary: UncheckedAccount<'info>,
    #[account(
        mut,
        associated_token::mint = usdc_mint,
        associated_token::authority = case,
        associated_token::token_program = token_program
    )]
    pub case_vault: InterfaceAccount<'info, TokenAccount>,
    #[account(
        init_if_needed,
        payer = payer,
        associated_token::mint = usdc_mint,
        associated_token::authority = beneficiary,
        associated_token::token_program = token_program
    )]
    pub beneficiary_ata: InterfaceAccount<'info, TokenAccount>,
    #[account(address = config.usdc_mint)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Refund<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(mut, seeds = [CASE_SEED, case.id.to_le_bytes().as_ref()], bump = case.bump)]
    pub case: Account<'info, Case>,
    #[account(
        mut,
        close = supporter,
        seeds = [PLEDGE_SEED, case.key().as_ref(), supporter.key().as_ref()],
        bump = pledge.bump,
        has_one = case,
        has_one = supporter
    )]
    pub pledge: Account<'info, Pledge>,
    #[account(
        mut,
        associated_token::mint = usdc_mint,
        associated_token::authority = case,
        associated_token::token_program = token_program
    )]
    pub case_vault: InterfaceAccount<'info, TokenAccount>,
    #[account(
        init_if_needed,
        payer = supporter,
        associated_token::mint = usdc_mint,
        associated_token::authority = supporter,
        associated_token::token_program = token_program
    )]
    pub supporter_ata: InterfaceAccount<'info, TokenAccount>,
    #[account(address = config.usdc_mint)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    #[account(mut)]
    pub supporter: Signer<'info>,
    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[error_code]
pub enum CivicError {
    #[msg("タイトルが長すぎます")]
    TitleTooLong,
    #[msg("URIが長すぎます")]
    UriTooLong,
    #[msg("期限が不正です")]
    InvalidDeadline,
    #[msg("金額は0より大きくしてください")]
    ZeroAmount,
    #[msg("このケースは支援受付を終了しています")]
    CaseClosed,
    #[msg("既に結論が提案または確定しています")]
    AlreadyResolved,
    #[msg("返金猶予期間に入ったため結論を受け付けられません")]
    ResolutionExpired,
    #[msg("結論が提案されていません")]
    NotProposed,
    #[msg("異議申立期間は終了しています")]
    ChallengeClosed,
    #[msg("異議申立期間中です")]
    ChallengeOpen,
    #[msg("異議申立されていません")]
    NotChallenged,
    #[msg("結論が確定していません")]
    NotFinal,
    #[msg("認容・一部認容ではありません")]
    NotGranted,
    #[msg("既に解放済みです")]
    AlreadyReleased,
    #[msg("返金条件を満たしていません")]
    RefundNotAvailable,
    #[msg("受益者は申立人本人または登録済みの有資格者に限られます")]
    BeneficiaryNotAllowed,
    #[msg("オーバーフロー")]
    Overflow,
}
