-- NTG production schema reconciliation for Supabase's October 30, 2026
-- Data API grant behavior change.
--
-- ACL-only reconciliation. It preserves table data, schema, RLS, policies,
-- indexes, constraints, functions, migration history before this entry, and
-- project-wide default privileges.
do $$
declare
  v_table text;
begin
  foreach v_table in array array[
    'ntg_subscription_fulfillments',
    'tax_quiz_questions',
    'tax_quiz_options',
    'tax_quiz_attempts'
  ] loop
    if to_regclass(format('public.%I', v_table)) is null then
      raise exception 'Required NTG table public.% is missing; refusing partial reconciliation', v_table;
    end if;
    if not (select c.relrowsecurity
            from pg_class c
            where c.oid = to_regclass(format('public.%I', v_table))) then
      raise exception 'RLS is not enabled on public.%; refusing privilege reconciliation', v_table;
    end if;
  end loop;
end
$$;

revoke all privileges on table public.ntg_subscription_fulfillments from public, anon, authenticated, service_role;
revoke all privileges on table public.tax_quiz_questions from public, anon, authenticated, service_role;
revoke all privileges on table public.tax_quiz_options from public, anon, authenticated, service_role;
revoke all privileges on table public.tax_quiz_attempts from public, anon, authenticated, service_role;

-- Current backend code calls ntg_fulfill_subscription_payment() as service_role.
-- The SECURITY DEFINER function runs as its postgres owner; direct table grants
-- are not needed by callers.
grant select on table public.tax_quiz_questions to service_role;
grant select on table public.tax_quiz_options to service_role;
grant select, insert, update on table public.tax_quiz_attempts to service_role;
