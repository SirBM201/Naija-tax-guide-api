-- NTG V1 — backfill already-applied legacy Paystack subscription references.
--
-- Purpose: payments fulfilled before ntg_subscription_fulfillments existed must
-- be registered as already consumed so a later Paystack webhook retry cannot
-- extend the subscription a second time.
--
-- This migration NEVER mutates user_subscriptions and therefore never extends
-- a paid period. It only records references whose legacy transaction metadata
-- explicitly says the subscription was already applied.

insert into public.ntg_subscription_fulfillments (
    reference,
    account_id,
    plan_code,
    duration_days,
    period_start,
    period_end
)
select
    pt.reference,
    pt.account_id,
    lower(pt.plan_code),
    case
        when lower(pt.plan_code) like '%yearly%' then 365
        when lower(pt.plan_code) like '%quarterly%' then 90
        else 30
    end as duration_days,
    greatest(
        coalesce(
            nullif(pt.metadata->>'applied_at', '')::timestamptz,
            nullif(pt.paid_at::text, '')::timestamptz,
            pt.created_at,
            now()
        ),
        coalesce(
            nullif(pt.metadata->>'expires_at', '')::timestamptz,
            us.current_period_end,
            us.expires_at,
            now()
        ) - make_interval(days => case
            when lower(pt.plan_code) like '%yearly%' then 365
            when lower(pt.plan_code) like '%quarterly%' then 90
            else 30
        end)
    ) as period_start,
    coalesce(
        nullif(pt.metadata->>'expires_at', '')::timestamptz,
        us.current_period_end,
        us.expires_at
    ) as period_end
from public.paystack_transactions pt
join lateral (
    select s.current_period_end, s.expires_at
    from public.user_subscriptions s
    where s.account_id = pt.account_id
    order by s.updated_at desc nulls last, s.created_at desc nulls last
    limit 1
) us on true
where pt.reference is not null
  and pt.account_id is not null
  and nullif(btrim(pt.plan_code), '') is not null
  and lower(coalesce(pt.status, '')) in ('success', 'paid', 'applied', 'completed')
  and (
      coalesce((pt.metadata->>'applied_subscription')::boolean, false)
      or lower(coalesce(pt.metadata->>'application_state', '')) in ('applied', 'already_applied')
  )
  and coalesce(
      nullif(pt.metadata->>'expires_at', '')::timestamptz,
      us.current_period_end,
      us.expires_at
  ) is not null
  and not exists (
      select 1
      from public.ntg_subscription_fulfillments f
      where f.reference = pt.reference
  )
on conflict (reference) do nothing;

-- Expected acceptance check for the known live Starter payment:
-- select reference, account_id, plan_code, period_start, period_end
-- from public.ntg_subscription_fulfillments
-- where reference = 'NTG-9962c5dda0d343dc9ee49764194a150c';
