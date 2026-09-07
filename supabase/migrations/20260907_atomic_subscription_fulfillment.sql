-- NTG V1 — atomic/idempotent paid subscription fulfillment
-- A verified Paystack reference may extend/activate a subscription at most once.
-- This RPC is intentionally service_role-only.

create table if not exists public.ntg_subscription_fulfillments (
    reference text primary key,
    account_id uuid not null,
    plan_code text not null,
    duration_days integer not null check (duration_days > 0),
    period_start timestamptz not null,
    period_end timestamptz not null,
    created_at timestamptz not null default now()
);

alter table public.ntg_subscription_fulfillments enable row level security;

revoke all on table public.ntg_subscription_fulfillments from anon, authenticated;
grant select, insert on table public.ntg_subscription_fulfillments to service_role;

create or replace function public.ntg_fulfill_subscription_payment(
    p_account_id uuid,
    p_plan_code text,
    p_reference text,
    p_duration_days integer
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_now timestamptz := now();
    v_existing public.ntg_subscription_fulfillments%rowtype;
    v_subscription public.user_subscriptions%rowtype;
    v_current_end timestamptz;
    v_period_start timestamptz;
    v_period_end timestamptz;
begin
    if p_account_id is null then raise exception 'account_id_required'; end if;
    if nullif(btrim(p_plan_code), '') is null then raise exception 'plan_code_required'; end if;
    if nullif(btrim(p_reference), '') is null then raise exception 'reference_required'; end if;
    if coalesce(p_duration_days, 0) <= 0 then raise exception 'invalid_duration_days'; end if;

    -- Serialize duplicate deliveries for the same Paystack reference.
    perform pg_advisory_xact_lock(hashtextextended(p_reference, 0));

    select * into v_existing
      from public.ntg_subscription_fulfillments
     where reference = p_reference;

    if found then
        if v_existing.account_id <> p_account_id or v_existing.plan_code <> p_plan_code then
            raise exception 'reference_fulfillment_mismatch';
        end if;
        return jsonb_build_object(
            'ok', true,
            'applied', false,
            'duplicate', true,
            'reference', v_existing.reference,
            'account_id', v_existing.account_id,
            'plan_code', v_existing.plan_code,
            'period_start', v_existing.period_start,
            'period_end', v_existing.period_end
        );
    end if;

    -- Serialize all subscription mutations for the account as well.
    perform pg_advisory_xact_lock(hashtextextended(p_account_id::text, 1));

    select * into v_subscription
      from public.user_subscriptions
     where account_id = p_account_id
     order by updated_at desc nulls last, created_at desc nulls last
     limit 1
     for update;

    if found then
        begin
            v_current_end := greatest(
                coalesce(v_subscription.current_period_end, '-infinity'::timestamptz),
                coalesce(v_subscription.expires_at, '-infinity'::timestamptz)
            );
        exception when undefined_column then
            v_current_end := coalesce(v_subscription.current_period_end, '-infinity'::timestamptz);
        end;

        if v_current_end is null or v_current_end = '-infinity'::timestamptz or v_current_end < v_now then
            v_period_start := v_now;
        else
            v_period_start := v_current_end;
        end if;
        v_period_end := v_period_start + make_interval(days => p_duration_days);

        update public.user_subscriptions
           set plan_code = p_plan_code,
               status = 'active',
               is_active = true,
               current_period_end = v_period_end,
               updated_at = v_now
         where id = v_subscription.id;

        -- Keep legacy expires_at synchronized when that column exists.
        begin
            execute 'update public.user_subscriptions set expires_at = $1 where id = $2'
                using v_period_end, v_subscription.id;
        exception when undefined_column then
            null;
        end;
    else
        v_period_start := v_now;
        v_period_end := v_now + make_interval(days => p_duration_days);
        insert into public.user_subscriptions(
            account_id, plan_code, status, is_active, current_period_end, created_at, updated_at
        ) values (
            p_account_id, p_plan_code, 'active', true, v_period_end, v_now, v_now
        ) returning * into v_subscription;
        begin
            execute 'update public.user_subscriptions set expires_at = $1 where id = $2'
                using v_period_end, v_subscription.id;
        exception when undefined_column then
            null;
        end;
    end if;

    update public.user_subscriptions
       set status = 'inactive', is_active = false, updated_at = v_now
     where account_id = p_account_id
       and id <> v_subscription.id
       and is_active = true;

    insert into public.ntg_subscription_fulfillments(
        reference, account_id, plan_code, duration_days, period_start, period_end
    ) values (
        p_reference, p_account_id, p_plan_code, p_duration_days, v_period_start, v_period_end
    );

    return jsonb_build_object(
        'ok', true,
        'applied', true,
        'duplicate', false,
        'reference', p_reference,
        'account_id', p_account_id,
        'plan_code', p_plan_code,
        'period_start', v_period_start,
        'period_end', v_period_end
    );
end;
$$;

revoke all on function public.ntg_fulfill_subscription_payment(uuid, text, text, integer) from public, anon, authenticated;
grant execute on function public.ntg_fulfill_subscription_payment(uuid, text, text, integer) to service_role;
