from __future__ import annotations

"""Static NTG V1 final launch gate.

This gate intentionally checks launch-critical controls without requiring
production credentials, network access or paid AI calls. Live provider smoke
checks remain a release-day acceptance item.
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
    payout = text("app/services/payout_service.py")
    payout_window = text("app/services/payout_window_service.py")
    assistant = text("app/services/guided_tax_assistant_service.py")
    telemetry = text("app/services/assistant_telemetry_service.py")
    source_guard = text("app/services/source_integrity_guard.py")
    calculator = text("app/services/tax_calculator.py")
    quiz = text("app/services/web_quiz_service.py")
    security = text("app/services/v1_security_guard.py")
    link = text("app/routes/link.py")

    require(init, "SESSION_COOKIE_HTTPONLY=True", "HTTP-only session cookie")
    require(init, "SESSION_COOKIE_SECURE", "secure session cookie")
    require(init, "CORS_ORIGINS='*' is not allowed with cookie auth", "credentialed CORS lock")
    require(main_py, "install_v1_security_guard(app)", "security guard activation")
    require(security, "rate_limited", "abuse rate limiting")
    require(security, "Content-Security-Policy", "security headers")
    require(security, "Cache-Control", "sensitive response no-store")

    require(webhook, "x-paystack-signature", "Paystack signature verification")
    require(webhook, "verify", "Paystack server verification")
    require(webhook, "fulfilled", "webhook fulfillment/idempotency state")

    require(link, "channel_entitlement", "channel entitlement enforcement")
    require(assistant, "source_integrity", "assistant source-integrity integration")
    require(telemetry, "rate", "assistant abuse/cost telemetry")
    require(source_guard, "review", "tax source review integrity")

    require(calculator, "credits_consumed", "free deterministic calculator accounting")
    require(quiz, "QUIZ_FREE_DAILY_LIMIT = 12", "free quiz daily limit")

    require(payout, "get_pending_or_processing_payout", "duplicate payout prevention")
    require(payout_window, "15th_and_30th", "referral payout schedule")

    print("NTG V1 final launch gate: STATIC PASS")
    print("Remaining release-day checks: live web auth, Paystack sandbox/live webhook, Telegram delivery, WhatsApp delivery, and authoritative 2026 Nigerian tax-rule review.")


if __name__ == "__main__":
    main()
