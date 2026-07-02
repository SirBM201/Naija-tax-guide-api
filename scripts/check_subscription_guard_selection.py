from datetime import datetime, timedelta, timezone

from app.services import subscription_guard


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main():
    now = datetime.now(timezone.utc)
    old_free = {
        "plan_code": "free",
        "status": "active",
        "is_active": True,
        "created_at": (now - timedelta(days=60)).isoformat(),
        "updated_at": (now - timedelta(days=60)).isoformat(),
        "expires_at": None,
    }
    paid_starter = {
        "plan_code": "starter_monthly",
        "status": "active",
        "is_active": True,
        "created_at": (now - timedelta(days=1)).isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    }
    expired_professional = {
        "plan_code": "professional_monthly",
        "status": "expired",
        "is_active": False,
        "created_at": (now - timedelta(days=2)).isoformat(),
        "updated_at": (now - timedelta(days=2)).isoformat(),
        "expires_at": (now - timedelta(days=1)).isoformat(),
    }

    ranked = sorted(
        [old_free, expired_professional, paid_starter],
        key=subscription_guard._subscription_rank,
        reverse=True,
    )

    assert_equal(ranked[0]["plan_code"], "starter_monthly", "latest active paid row should win")
    assert_equal(subscription_guard._subscription_is_active_now(ranked[0]), True, "paid row active check")
    assert_equal(subscription_guard._has_paid_plan(ranked[0]), True, "paid row paid-plan check")

    print("Subscription guard selection checks passed")


if __name__ == "__main__":
    main()
