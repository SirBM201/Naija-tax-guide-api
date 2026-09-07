from __future__ import annotations

"""V1 adapter that routes channel natural-language Ask calls through the Guided Tax Assistant.

This preserves the mature WhatsApp/Telegram command engines and only replaces
their imported ask_guarded callable. Deterministic product guidance therefore
costs no AI credits, while real tax questions retain the existing guarded
knowledge/paid-AI path.
"""

from typing import Any, Dict

GUIDED_CHANNEL_PATCH_VERSION = "2026-09-07-v1-04b"


def _guided_ask(payload: Any = None, **kwargs: Any) -> Dict[str, Any]:
    from app.services.guided_tax_assistant_service import guide_or_answer

    data: Dict[str, Any] = dict(payload or {}) if isinstance(payload, dict) else {}
    data.update(kwargs)
    account_id = str(data.pop("account_id", "") or "").strip()
    message = str(
        data.pop("question", "")
        or data.pop("query", "")
        or data.pop("text", "")
        or data.pop("message", "")
        or ""
    ).strip()
    lang = str(data.pop("lang", "en") or "en").strip()
    channel = str(data.pop("channel", data.get("provider") or "web") or "web").strip().lower()
    provider_user_id = str(data.pop("provider_user_id", "") or "").strip()
    action_code = str(data.pop("action_code", "ai_tax_answer") or "ai_tax_answer").strip()
    data.pop("provider", None)
    return guide_or_answer(
        account_id=account_id,
        message=message,
        lang=lang,
        channel=channel,
        provider_user_id=provider_user_id,
        action_code=action_code,
        **data,
    )


def apply_guided_channel_patch() -> None:
    # Importing these modules here is safe during app startup; create_app later
    # registers the same module-level blueprint objects.
    for module_name in ("app.routes.telegram", "app.routes.whatsapp", "app.routes.inbound"):
        try:
            module = __import__(module_name, fromlist=["ask_guarded"])
            if hasattr(module, "ask_guarded"):
                setattr(module, "ask_guarded", _guided_ask)
        except Exception:
            # Channels remain operational on their legacy guarded Ask path if an
            # optional channel module is unavailable in a deployment.
            continue
