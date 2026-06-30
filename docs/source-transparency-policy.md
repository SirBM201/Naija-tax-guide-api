# Source Transparency Policy

Last reviewed: 30 June 2026
Product: Naija Tax Guide
Owner: BMS SparkVision Hub

## Purpose

Naija Tax Guide should make tax answers more trustworthy by tracking where sensitive claims come from, when they were reviewed, and whether they are safe for direct user guidance.

## Current implementation added

The backend now includes `app/services/tax_source_catalog.py`, which defines source categories and risk levels for future integration with curated answers and channel responses.

Current categories:

- `primary_law`
- `federal_authority_guidance`
- `state_authority_practice`
- `reviewed_internal_answer`

A verification script is available at `scripts/check_source_catalog.py`.

## Claims that need source discipline

Treat these as high-risk unless reviewed recently:

- tax rates and thresholds;
- filing and payment deadlines;
- penalties, interest, and enforcement consequences;
- effective dates for new laws, reforms, or transitional rules;
- official portal routes and payment instructions;
- state-specific PAYE or personal income tax processes;
- audit, dispute, objection, appeal, and official notice handling.

## Recommended metadata fields

Curated answer records should eventually support:

- `source_category`
- `source_name`
- `source_url`
- `jurisdiction`
- `tax_year`
- `risk_level`
- `last_reviewed_at`
- `reviewed_by`
- `needs_reverification`

## Display rules

For low-risk education answers:

- show normal guidance note;
- mention assumptions where needed.

For medium-risk answers:

- show source category if available;
- show last-reviewed date where available;
- ask for missing facts before final conclusions.

For high-risk answers:

- show source category and review date where available;
- warn that current official guidance should be verified;
- escalate audit, dispute, penalty, formal filing, and high-value decisions.

## Channel behavior

The same source metadata should eventually appear across:

- web Ask responses;
- WhatsApp answers;
- Telegram answers;
- PDF receipts or generated documents where tax calculations are shown.

## Acceptance criteria

Source transparency is ready for external review when:

- common curated answers have source category and last-reviewed date;
- stale or missing source metadata is visible internally;
- high-risk answers do not appear without caution;
- web, WhatsApp, and Telegram display substantially consistent caution language;
- reviewer test prompts include source/date checks.
