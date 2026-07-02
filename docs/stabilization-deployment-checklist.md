# Naija Tax Guide API - Stabilization Deployment Checklist

Use this after backend deployments that touch billing, subscriptions, workspace limits, WhatsApp, Telegram, or auth.

## 1. Run deterministic local checks where possible

```bash
python scripts/check_ai_safety_policy.py
python scripts/check_source_catalog.py
python scripts/check_subscription_guard_selection.py
```

These scripts do not require OpenAI or Paystack calls.

## 2. Confirm health endpoints

Check:

- `/api/health`
- `/api/billing/me` while logged in
- `/api/workspace/limits` while logged in
- `/api/link/status` while logged in

Expected:

- `/api/billing/me` and `/api/workspace/limits` must agree on the active plan.
- A paid account must not show `plan_code=free` in workspace limits.

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

## 5. WhatsApp and Telegram state retest

After a successful plan activation:

- WhatsApp plan display should match web billing.
- Telegram plan display should match web billing.
- Top-up should add credits without extending plan validity.
- Subscription payment should activate or extend the plan according to billing rules.

## 6. Stable deployment criteria

A backend deployment is stable when:

- Health endpoints return OK.
- Paid billing and workspace limits agree.
- Old Free rows cannot override active paid rows.
- Payment verification is recoverable by reopening `/billing/success?reference=...&plan=...`.
- WhatsApp and Telegram do not contradict web billing state.
