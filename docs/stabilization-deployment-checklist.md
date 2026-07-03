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

## 2. Apply metadata migration before expecting stored metadata

Run the Supabase migration:

```bash
supabase/migrations/20260703_answer_source_metadata.sql
```

Then dry-run the backfill:

```bash
DRY_RUN=1 LIMIT=500 TABLES=qa_library,qa_cache python scripts/backfill_answer_source_metadata.py
```

Apply when satisfied:

```bash
DRY_RUN=0 LIMIT=500 TABLES=qa_library,qa_cache python scripts/backfill_answer_source_metadata.py
```

## 3. Confirm health endpoints

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

## 4. Confirm subscription-row selection

For paid users with older free rows in `user_subscriptions`, backend code must select the latest active paid subscription.

Expected Supabase pattern for core subscription guard:

- table: `user_subscriptions`
- account filter: `account_id=eq.<account_id>`
- ordered lookup where possible: `order=updated_at.desc`
- enough rows to rank locally, not only the first unordered row

## 5. Payment activation retest

Expected flow:

1. Paystack initializes checkout.
2. User returns to `/billing/success`.
3. `/api/billing/verify` verifies the reference.
4. `/api/billing/me` shows the paid plan.
5. `/api/workspace/limits` shows the same paid plan and paid limits.

## 6. Source metadata and AI safety retest

Use logged-in Ask on web, then repeat at least one question through WhatsApp and Telegram.

Expected:

- Successful answers include a guidance note.
- Source-sensitive answers include a source/freshness note.
- Successful answers include `Source details:` in the rendered answer text.
- API response `meta.source_metadata` should be present on successful Ask answers.
- Sensitive cases should recommend professional review or a qualified professional.

## 7. Expert review workflow retest

While logged in, test:

- `GET /api/expert-review/packages`
- `POST /api/expert-review/request`
- Frontend `/expert-review`

Expected:

- A professional-review request creates a tracked support ticket.
- The ticket stores a schema-compatible support category while the subject/message preserve professional-review routing.
- The user should receive a ticket ID and can track the request from Support.

## 8. Admin evidence checks

Requires backend admin key through `X-Admin-Key`.

Check:

```bash
curl -H "X-Admin-Key: <admin_key>" "https://<api-host>/api/expert-review/admin/queue?limit=100"
curl -H "X-Admin-Key: <admin_key>" "https://<api-host>/api/expert-review/admin/source-coverage?limit=1000&stale_days=180"
```

Frontend admin page:

```text
/admin/expert-review
```

Expected:

- Queue endpoint returns professional-review requests.
- Coverage endpoint returns source metadata coverage for `qa_library`, `qa_cache`, and `qa_history`.
- Frontend admin page can load both after entering the admin key.

## 9. WhatsApp and Telegram state retest

After a successful plan activation:

- WhatsApp plan display should match web billing.
- Telegram plan display should match web billing.
- Top-up should add credits without extending plan validity.
- Subscription payment should activate or extend the plan according to billing rules.
- Ask answers through both channels should include guidance and source/freshness language where relevant.

## 10. Stable deployment criteria

A backend deployment is stable when:

- Health endpoints return OK.
- Paid billing and workspace limits agree.
- Old Free rows cannot override active paid rows.
- Payment verification is recoverable by reopening `/billing/success?reference=...&plan=...`.
- Web, WhatsApp, and Telegram do not contradict billing state.
- Source metadata appears in successful Ask answers.
- Expert review request creation works and creates a trackable ticket.
- Admin evidence endpoints work with the backend admin key.
