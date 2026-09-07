# Naija Tax Guide — V1 Completion Execution Plan

Status: **ACTIVE — V1 completion only**

This plan governs the final Naija Tax Guide V1 implementation. No post-V1 feature expansion should interrupt this sequence.

## NTG-V1-01 — Baseline and launch-gap audit

- Reconcile API, frontend, billing, channels, entitlements, referral, calculators, quiz, tax-content safety, source transparency and deployment state.
- Classify each launch requirement as PASS, PARTIAL or BLOCKED.
- Remove obsolete/duplicate launch assumptions from the active acceptance path.

## NTG-V1-02 — Core account, plan and entitlement integrity

- Verify Free Forever behavior.
- Preserve free calculators and database guidance.
- Verify Starter Monthly entitlement and approved channel limits.
- Verify paid-only AI/top-up boundaries.
- Ensure account linking/unlinking cannot bypass entitlement rules.

## NTG-V1-03 — Billing and Paystack production integrity

- Verify checkout initialization, references, webhook signature validation, idempotency and payment reconciliation.
- Verify subscription/credit fulfillment occurs only after trusted server-side payment verification.
- Verify failed/duplicate/replayed events cannot double-credit an account.
- Verify user-facing payment return/recovery states.

## NTG-V1-04 — Guided AI Tax Assistant + cost guardrail

- Implement the V1 guided assistant requirement.
- Route requests through: **Database/rules → cached/approved knowledge → low-cost AI → advanced AI**.
- Deterministic navigation, calculators and database answers must not consume paid AI inference unnecessarily.
- Enforce plan/credit entitlement before paid AI use.
- Add usage/cost telemetry, rate limits and configurable cost ceilings.
- Preserve tax-source/effective-date transparency and safety boundaries.

## NTG-V1-05 — Tax answer accuracy and source integrity

- Verify approved-source retrieval and answer metadata.
- Ensure stale/uncertain/high-risk tax questions fail safely or communicate uncertainty.
- Verify source dates/effective dates are exposed where available.
- Run the existing tax expert/reviewer benchmark and repair regressions.

## NTG-V1-06 — Calculators, quiz and free-value acceptance

- Verify calculators remain free and deterministic.
- Verify approved Free Forever non-AI quiz allowance and reset behavior.
- Verify calculator/quiz activity cannot incorrectly debit AI credits.
- Verify mobile and error states.

## NTG-V1-07 — Telegram/WhatsApp/web channel acceptance

- Verify supported V1 menus/actions and account identity/linking.
- Verify plan-based channel access and approved Starter channel limit.
- Verify channel failures do not corrupt balances, subscriptions or account state.
- Treat unavailable external-provider setup as an explicit launch dependency rather than silently passing it.

## NTG-V1-08 — Referral and payout integrity

- Verify L1/L2 referral attribution, hold rules, payout eligibility and duplicate/self-referral defenses according to the approved V1 policy.
- Verify referral rewards cannot be created from unverified payment events.

## NTG-V1-09 — Security, privacy and abuse hardening

- Review authentication/authorization boundaries, admin endpoints, webhook endpoints, secrets handling, rate limits and sensitive logging.
- Verify production errors do not expose secrets or internal stack data.

## NTG-V1-10 — Production acceptance and launch gate

- Run backend regression/CI and frontend production acceptance.
- Validate critical user journeys: free user, paid user, payment, AI entitlement, calculator, quiz, linking and recovery/failure states.
- Re-run deployment/stabilization checklist.
- V1 is complete only when all critical launch requirements PASS or an external dependency is explicitly documented and accepted.

## Scope lock

During this execution sequence, new ideas are parked for post-V1 unless required to fix a launch blocker, security issue, compliance issue, billing integrity problem or the already-approved V1 Guided AI Tax Assistant.