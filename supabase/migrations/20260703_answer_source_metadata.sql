-- Naija Tax Guide - answer source metadata fields
-- Purpose: allow curated/cache/history answers to store source, review, jurisdiction, tax year, and risk controls.
-- Safe to run more than once because every column uses IF NOT EXISTS.

alter table if exists public.qa_library
  add column if not exists source_category text,
  add column if not exists source_label text,
  add column if not exists source_url text,
  add column if not exists source_type text,
  add column if not exists jurisdiction text default 'Nigeria',
  add column if not exists tax_year text,
  add column if not exists risk_level text default 'medium',
  add column if not exists last_reviewed_at timestamptz,
  add column if not exists reviewed_by text,
  add column if not exists reviewer_notes text,
  add column if not exists source_metadata jsonb default '{}'::jsonb;

alter table if exists public.qa_cache
  add column if not exists source_category text,
  add column if not exists source_label text,
  add column if not exists source_url text,
  add column if not exists source_type text,
  add column if not exists jurisdiction text default 'Nigeria',
  add column if not exists tax_year text,
  add column if not exists risk_level text default 'medium',
  add column if not exists last_reviewed_at timestamptz,
  add column if not exists reviewed_by text,
  add column if not exists reviewer_notes text,
  add column if not exists source_metadata jsonb default '{}'::jsonb;

alter table if exists public.qa_history
  add column if not exists source_category text,
  add column if not exists source_label text,
  add column if not exists source_type text,
  add column if not exists jurisdiction text default 'Nigeria',
  add column if not exists tax_year text,
  add column if not exists risk_level text,
  add column if not exists last_reviewed_at timestamptz,
  add column if not exists source_metadata jsonb default '{}'::jsonb;

create index if not exists idx_qa_library_source_category on public.qa_library(source_category);
create index if not exists idx_qa_library_risk_level on public.qa_library(risk_level);
create index if not exists idx_qa_library_last_reviewed_at on public.qa_library(last_reviewed_at);

create index if not exists idx_qa_cache_source_category on public.qa_cache(source_category);
create index if not exists idx_qa_cache_risk_level on public.qa_cache(risk_level);
create index if not exists idx_qa_cache_last_reviewed_at on public.qa_cache(last_reviewed_at);

create index if not exists idx_qa_history_source_category on public.qa_history(source_category);
create index if not exists idx_qa_history_risk_level on public.qa_history(risk_level);
