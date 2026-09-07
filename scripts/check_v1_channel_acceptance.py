from __future__ import annotations

"""Static V1 acceptance gate for web/WhatsApp/Telegram shared behaviour.

Run from repository root:
    python scripts/check_v1_channel_acceptance.py

This gate intentionally checks architecture/policy wiring without external
network calls, provider credentials or paid AI inference.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    main_py = read("app/main.py")
    guided = read("app/services/guided_tax_assistant_service.py")
    patch = read("app/services/guided_channel_patch.py")
    runtime_guard = read("app/services/channel_runtime_guard.py")
    runtime_http_guard = read("app/services/channel_runtime_http_guard.py")
    activation = read("app/services/channel_activation_service.py")
    web = read("app/routes/web_ask.py")
    quiz = read("app/services/web_quiz_service.py")
    calc = read("app/services/tax_calculator.py")
    credit = read("app/services/channel_credit_service.py")
    telegram = read("app/routes/telegram.py")
    whatsapp = read("app/routes/whatsapp.py")

    # All three V1 surfaces must route natural-language tax guidance through the
    # same guarded assistant rather than maintaining divergent AI policies.
    require("guide_or_answer" in web, "web ask is not wired to guided assistant")
    require('"app.routes.telegram"' in patch, "Telegram guided adapter missing")
    require('"app.routes.whatsapp"' in patch, "WhatsApp guided adapter missing")
    require("guide_or_answer" in patch, "channel adapter does not call guided assistant")

    # Free product value must remain deterministic/non-AI.
    require('"calculation_mode"' in read("app/services/calculator_rule_metadata.py"), "calculator free-mode metadata missing")
    require("attach_calculator_metadata" in calc, "calculator rule metadata not attached")
    require("QUIZ_FREE_DAILY_LIMIT = 12" in quiz, "Free Forever quiz limit is not 12/day")
    require("usage_charged" in guided and "credits_consumed" in guided, "assistant cost metadata missing")

    # Paid credit top-up remains subscriber-only; do not let channel differences
    # bypass the account entitlement policy.
    require("has_active_subscription" in credit, "credit service lacks paid subscription guard")

    # Durable connection and active entitlement are intentionally separate.
    require("selection_required" in activation, "activation service lacks over-limit selection state")
    require('"paused"' in activation, "activation service lacks paused durable-channel state")
    require("is_active" in activation, "activation service lacks explicit active state")
    require("check_channel_runtime_access" in runtime_http_guard, "HTTP webhook guard is not wired to runtime entitlement guard")
    require("install_channel_runtime_http_guard(app)" in main_py, "runtime channel HTTP guard is not installed")
    require("request.method != \"POST\"" in runtime_http_guard, "provider verification/health GETs are not exempted")
    require("channel_runtime_blocked" in runtime_http_guard, "blocked channel webhook is not safely acknowledged")
    require("return jsonify" in runtime_http_guard and "), 200" in runtime_http_guard, "blocked provider webhook must return HTTP 200")
    require("paused" in runtime_guard.lower(), "runtime entitlement guard lacks paused-channel enforcement")
    require("selection_required" in runtime_guard, "runtime entitlement guard lacks selection-required enforcement")

    # Mature channel command engines must still exist after assistant integration.
    require("calculate_tax" in telegram, "Telegram calculator flow missing")
    require("calculate_tax" in whatsapp, "WhatsApp calculator flow missing")
    require("quiz" in telegram.lower(), "Telegram quiz flow missing")
    require("quiz" in whatsapp.lower(), "WhatsApp quiz flow missing")

    print("NTG V1 channel acceptance: STATIC PASS")
    print("- Web natural-language ask -> Guided Tax Assistant")
    print("- WhatsApp natural-language ask -> shared Guided Tax Assistant adapter")
    print("- Telegram natural-language ask -> shared Guided Tax Assistant adapter")
    print("- Calculators remain deterministic/free")
    print("- Free quiz allowance remains 12 non-AI attempts/day")
    print("- Paid credit top-up guard remains present")
    print("- Durable channel connections remain preserved separately from active entitlement")
    print("- Paused/over-limit WhatsApp and Telegram service consumption is blocked before route dispatch")
    print("- Blocked provider webhooks are safely acknowledged with HTTP 200 to avoid retry storms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
