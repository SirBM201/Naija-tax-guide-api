-- NTG V1: atomic, reference-idempotent Paystack credit fulfillment.
-- Apply in Supabase before enabling the RPC path in production.

create or replace function public.ntg_fulfill_credit_purchase(
  p_account_id uuid,
  p_credits integer,
  p_reference text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_existing_event uuid;
  v_balance integer;
begin
  if p_account_id is null or coalesce(p_credits, 0) <= 0 or nullif(trim(p_reference), '') is null then
    return jsonb_build_object('ok', false, 'error', 'invalid_fulfillment_arguments');
  end if;

  -- Serialize all attempts for the same gateway reference, even before an
  -- event row exists. This closes the webhook read-before-write race.
  perform pg_advisory_xact_lock(hashtextextended('ntg-paystack:' || p_reference, 0));

  select id into v_existing_event
  from public.ai_credit_events
  where reference = p_reference
    and event_type = 'credit_purchase'
  limit 1;

  if v_existing_event is not null then
    select balance into v_balance
    from public.ai_credit_balances
    where account_id = p_account_id
    limit 1;
    return jsonb_build_object('ok', true, 'duplicate', true, 'credited', false, 'balance', coalesce(v_balance, 0));
  end if;

  insert into public.ai_credit_balances(account_id, balance, updated_at)
  values (p_account_id, p_credits, now())
  on conflict (account_id) do update
    set balance = public.ai_credit_balances.balance + excluded.balance,
        updated_at = now()
  returning balance into v_balance;

  insert into public.ai_credit_events(account_id, event_type, credits, reference, created_at)
  values (p_account_id, 'credit_purchase', p_credits, p_reference, now());

  return jsonb_build_object('ok', true, 'duplicate', false, 'credited', true, 'credits', p_credits, 'balance', v_balance);
exception
  when others then
    raise;
end;
$$;

revoke all on function public.ntg_fulfill_credit_purchase(uuid, integer, text) from public;
revoke all on function public.ntg_fulfill_credit_purchase(uuid, integer, text) from anon;
revoke all on function public.ntg_fulfill_credit_purchase(uuid, integer, text) from authenticated;
grant execute on function public.ntg_fulfill_credit_purchase(uuid, integer, text) to service_role;
