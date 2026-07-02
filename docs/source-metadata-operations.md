# Source Metadata Operations Guide

Purpose: keep Naija Tax Guide answers traceable, reviewable, and safer for users.

## What source metadata covers

Successful Ask answers can now carry structured metadata such as:

- source category
- source label
- source type
- jurisdiction
- tax year
- risk level
- review status
- last reviewed date
- source/freshness sensitivity

The user-facing answer can also show a `Source details:` note so users know whether the answer is AI-generated, reviewed internally, or tied to a source category.

## Database fields

Migration:

```bash
supabase/migrations/20260703_answer_source_metadata.sql
```

Tables covered:

- `qa_library`
- `qa_cache`
- `qa_history`

Core fields:

- `source_category`
- `source_label`
- `source_url`
- `source_type`
- `jurisdiction`
- `tax_year`
- `risk_level`
- `last_reviewed_at`
- `reviewed_by`
- `reviewer_notes`
- `source_metadata`

## Backfill existing rows

Dry run first:

```bash
DRY_RUN=1 LIMIT=500 TABLES=qa_library,qa_cache python scripts/backfill_answer_source_metadata.py
```

Apply updates:

```bash
DRY_RUN=0 LIMIT=500 TABLES=qa_library,qa_cache python scripts/backfill_answer_source_metadata.py
```

Important:

- The script does not invent a reviewed date.
- It only uses an existing review/update date where available.
- It builds source metadata using current row fields plus the answer metadata service.

## Admin coverage endpoint

Requires backend admin key through `X-Admin-Key`.

```bash
curl -H "X-Admin-Key: <admin_key>" \
  "https://<api-host>/api/expert-review/admin/source-coverage?limit=1000&stale_days=180"
```

Expected response includes coverage for:

- sampled rows
- missing source category
- missing review date
- stale review count
- high-risk count
- sample rows needing attention

## Admin web page

Frontend route:

```text
/admin/expert-review
```

Use it to enter the backend admin key and load:

- professional-review queue
- source metadata coverage

## Regression checks

Run after backend changes:

```bash
python scripts/check_answer_metadata_policy.py
python scripts/check_ai_safety_policy.py
python scripts/check_source_catalog.py
```

## Operational rules

1. Do not publicly claim that answers are expert-reviewed until reviewer outcomes have actually been recorded.
2. Source-sensitive answers should continue to show source/freshness caution.
3. Curated answers should gradually be populated with review date, jurisdiction, tax year, and risk level.
4. Rows missing source metadata should be treated as lower-trust until reviewed.
5. High-risk rows should be prioritized for expert review.
