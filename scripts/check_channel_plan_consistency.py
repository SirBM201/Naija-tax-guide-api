import os
from datetime import datetime, timezone
from pprint import pprint

from app.core.supabase_client import get_supabase_client
from app.services.subscription_guard import get_subscription_snapshot


def clean(value):
    return str(value or "").strip()


def rows(resp):
    data = getattr(resp, "data", None) or []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def main():
    account_id = clean(os.getenv("NTG_ACCOUNT_ID"))
    if not account_id:
        raise SystemExit("Set NTG_ACCOUNT_ID before running this script.")

    sb = get_supabase_client(admin=True)

    print("Naija Tax Guide channel plan consistency check")
    print("Account:", account_id)
    print("Checked at:", datetime.now(timezone.utc).isoformat())
    print()

    snapshot = get_subscription_snapshot(account_id)
    print("Subscription snapshot:")
    pprint(
        {
            "ok": snapshot.get("ok"),
            "plan_code": snapshot.get("plan_code"),
            "plan_family": snapshot.get("plan_family"),
            "active_now": snapshot.get("active_now"),
            "access": snapshot.get("access"),
            "channel_limits": snapshot.get("channel_limits"),
            "user_limits": snapshot.get("user_limits"),
        }
    )
    print()

    sub_rows = rows(
        sb.table("user_subscriptions")
        .select("*")
        .eq("account_id", account_id)
        .order("updated_at", desc=True)
        .limit(10)
        .execute()
    )
    print("Recent user_subscriptions rows:")
    for row in sub_rows:
        pprint(
            {
                "id": row.get("id"),
                "plan_code": row.get("plan_code"),
                "status": row.get("status"),
                "is_active": row.get("is_active"),
                "current_period_end": row.get("current_period_end"),
                "expires_at": row.get("expires_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    print()

    identities = rows(
        sb.table("channel_identities")
        .select("*")
        .eq("account_id", account_id)
        .limit(20)
        .execute()
    )
    print("Channel identities:")
    if not identities:
        print("No channel_identities rows found for this account.")
    for row in identities:
        pprint(
            {
                "id": row.get("id"),
                "channel_type": row.get("channel_type"),
                "provider_user_id": row.get("provider_user_id"),
                "is_verified": row.get("is_verified"),
                "linked_at": row.get("linked_at"),
                "last_seen_at": row.get("last_seen_at"),
            }
        )
    print()

    account_rows = rows(
        sb.table("accounts")
        .select("account_id,id,provider,provider_user_id,auth_user_id,email,phone,phone_e164,display_name,updated_at")
        .or_(f"account_id.eq.{account_id},id.eq.{account_id},auth_user_id.eq.{account_id}")
        .limit(20)
        .execute()
    )
    print("Related accounts rows:")
    if not account_rows:
        print("No related accounts rows found through account_id/id/auth_user_id.")
    for row in account_rows:
        pprint(row)

    plan_code = clean(snapshot.get("plan_code"))
    if not plan_code or plan_code in {"free", "free_forever"}:
        raise SystemExit("FAIL: subscription snapshot is not resolving a paid plan.")

    print()
    print("PASS: subscription snapshot resolves a paid plan. Confirm WhatsApp/Telegram bot menus show the same plan.")


if __name__ == "__main__":
    main()
