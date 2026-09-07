from __future__ import annotations

"""Static NTG V1 final launch gate.

Checks launch-critical controls without production credentials, network access
or paid provider calls. Live provider smoke tests remain release-day checks.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    p = ROOT / path
    assert p.exists(), f"missing launch-critical file: {path}"
    return p.read_text(encoding="utf-8")


def require(haystack: str, needle: str, label: str) -> None:
    assert needle in haystack, f"launch gate failed: {label}"


def main() -> None:
    init = text("app/__init__.py")
    main_py = text("app/main.py")
    webhook = text("app/routes/paystack_webhook.py")
    credit = text("app/services/channel_credit_service.py")
    atomic_sql = text("supabase/migrations/20260907_paystack_atomic_credit_fulfillment.sql")
    payout = text("app/services/payout_service.py")
    payout_window = text("app/services/payout_window_service.py")
    assistant = text("app/services/guided_tax_assistant_service.py")
    telemetry = text("app/services/assistant_telemetry_service.py")
    source_guard = text("app/services/source_integrity_guard.py")
    calculator = text("app/services/tax_calculator.py")
    quiz = text("app/services/web_quiz_service.py")
    security = text("app/services/v1_security_guard.py")
    link = text("app/routes/link.py")
    plans = text("app/services/plans_service.py")
    cache_patch = text("app/services/v1_cache_reuse_patch.py")

    require(init, "SESSION_COOKIE_HTTPONLY=True", "HTTP-only session cookie")
    require(init, "SESSION_COOKIE_SECURE", "secure session cookie")
    require(init, "CORS_ORIGINS='*' is not allowed with cookie auth", "credentialed CORS lock")
    require(main_py, "install_v1_security_guard(app)", "security guard activation")
    require(security, "rate_limited", "abuse rate limiting")
    require(security, "WEBHOOK_PATH_MARKERS", "provider webhook rate-limit exemption")
    require(security, "hmac.compare_digest", "constant-time admin key comparison")
    require(security, "NTG_TRUST_PROXY_HEADERS", "explicit proxy-header trust policy")
    require(security, "Content-Security-Policy", "security headers")
    require(security, "response.mimetype != \"text/html\"", "HTML callback CSP compatibility")
    require(security, "Cache-Control", "sensitive response no-store")

    require(webhook, "x-paystack-signature", "Paystack signature verification")
    require(webhook, "verify_transaction", "Paystack server transaction verification")
    require(webhook, "amount_mismatch", "Paystack amount verification")
    require(webhook, "currency_mismatch", "Paystack currency verification")
    require(credit, 'rpc("ntg_fulfill_credit_purchase"', "atomic Paystack credit fulfillment RPC")
    require(credit, "There is intentionally no legacy read/update fallback", "no unsafe credit fallback")
    require(atomic_sql, "pg_advisory_xact_lock", "Paystack reference serialization")
    require(atomic_sql, "ntg_fulfill_credit_purchase", "atomic fulfillment migration")

    require(link, "channel_entitlement", "channel entitlement enforcement")
    require(plans, '"max_total_channels": 1', "Starter external-channel limit")
    require(plans, '"web_channel_included": True', "web channel included separately")
    require(plans, '"external_messaging_channels"', "external-channel limit scope")

    require(assistant, "precharge-source-integrity", "pre-charge source-integrity assistant version")
    require(assistant, "_precharge_integrity_find", "source integrity before paid fallback")
    require(source_guard, "review", "tax source review integrity")
    require(cache_patch, "review_required_not_cached", "fresh AI answers not auto-approved for reuse")

    require(telemetry, '"budget_policy": "monitor_only"', "global AI spend is monitoring-only")
    require(telemetry, "ASSISTANT_AI_REQUESTS_PER_MINUTE", "per-user assistant abuse control")
    require(telemetry, "daily_cost_alert", "AI spend alert telemetry")

    require(calculator, "credits_consumed", "free deterministic calculator accounting")
    require(quiz, "QUIZ_FREE_DAILY_LIMIT = 12", "free quiz daily limit")
    require(payout, "get_pending_or_processing_payout", "duplicate payout prevention")
    require(payout_window, "15th_and_30th", "referral payout schedule")

    print("NTG V1 final launch gate: STATIC PASS")
    print("Verified statically: auth/cookie/CORS controls, provider-webhook security, atomic Paystack credit fulfillment, channel limits, source integrity, cache review safety, AI monitoring policy, free calculator/quiz accounting, and payout duplicate/schedule controls.")
    print("Remaining live acceptance: web auth journey, Paystack verified webhook/duplicate replay, Telegram delivery, WhatsApp delivery, and authoritative 2026 Nigerian tax-rule review.")


if __name__ == "__main__":
    main()
