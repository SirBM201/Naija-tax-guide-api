# Naija Tax Guide API - Stabilization Deployment Checklist

Use this after backend deployments that touch billing, subscriptions, workspace limits, AI answers, source metadata, WhatsApp, Telegram, expert review, or auth.

## 1. Run deterministic local checks where possible

```bash
python scripts/check_ai_safety_policy.py
python scripts/check_source_catalog.py
python scripts/check_answer_metadata_policy.py
python scripts/check_subscription_guard_selection.py
```

For a specific paid account, also run:

```bash
NTG_ACCOUNT_ID=<account_id> python scripts/check_channel_plan_consistency.py
```

These scripts do not require OpenAI or Paystack calls. The channel plan consistency script requires Supabase admin credentials and a real account ID.

## 2. Confirm health endpoints

Check:

- `/api/health`
- `/api/ask/health`
- `/api/billing/me` while logged in
- `/api/workspace/limits` while logged in
- `/api/link/status` while logged in
- `/api/expert-review/health`
- `/api/expert-review/packages`

Expected:

- `/api/billing/me` and `/api/workspace/limits` must agree on the active plan.
- A paid account must not show `plan_code=free` in workspace limits.
- Expert review packages should return `triage`, `notice_review`, and `filing_review`.

## 3. Confirm subscription-row selection in logs

For paid users with older free rows in `user_subscriptions`, backend code must select the latest active paid subscription.

Expected Supabase pattern for core subscription guard:

- table: `user_subscriptions`
- account filter: `account_id=eq.<account_id>`
- ordered lookup where possible: `order=updated_at.desc`
- enough rows to rank locally, not only the first unordered row

Why this matters:

An unordered `.limit(1)` query can return an old Free row even when a paid subscription exists. That causes Dashboard, Channels, Workspace, sidebar badges, and other workspace-limit consumers to show Free after successful Paystack payment.

## 4. Payment activation retest

Use a safe test payment path or a low-risk live checkout.

Expected flow:

1. Paystack initializes checkout.
2. User returns to `/billing/success`.
3. `/api/billing/verify` verifies the reference.
4. `/api/billing/me` shows the paid plan.
5. `/api/workspace/limits` shows the same paid plan and paid limits.

## 5. Source metadata and AI safety retest

Ask these as a logged-in paid user through web Ask, then repeat at least one through WhatsApp and Telegram:

- A simple PAYE question.
- A VAT filing deadline question.
- A question involving an official tax document, notice, assessment, or penalty.
- A question involving a rate, threshold, due date, portal, or filing procedure.

Expected:

- Successful answers include a guidance note.
- Source-sensitive answers include a source/freshness note.
- Successful answers include `Source details:` in the rendered answer text.
- API response `meta.source_metadata` should be present on successful Ask answers.
- High-risk cases should recommend professional review or a qualified tax professional.

## 6. Expert review workflow retest

While logged in, test:

- `GET /api/expert-review/packages`
- `POST /api/expert-review/request`
- Frontend `/expert-review`

Expected:

- A professional-review request creates a tracked support ticket.
- The ticket category should be `professional_review` where the live table supports it.
- The user should receive a ticket ID and can track the request from Support.

## 7. WhatsApp and Telegram state retest

After a successful plan activation:

- WhatsApp plan display should match web billing.
- Telegram plan display should match web billing.
- Top-up should add credits without extending plan validity.
- Subscription payment should activate or extend the plan according to billing rules.
- Ask answers through both channels should include guidance and source/freshness language where relevant.

## 8. Stable deployment criteria

A backend deployment is stable when:

- Health endpoints return OK.
- Paid billing and workspace limits agree.
- Old Free rows cannot override active paid rows.
- Payment verification is recoverable by reopening `/billing/success?reference=...&plan=...`.
- Web, WhatsApp, and Telegram do not contradict billing state.
- Source metadata appears in successful Ask answers.
- Expert review request creation works and creates a trackable ticket.
