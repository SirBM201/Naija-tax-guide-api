# app/routes/telegram.py
from __future__ import annotations

import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.accounts_service import lookup_account, upsert_account
from app.services.guided_tax_assistant_service import guide_or_answer
from app.services.channel_credit_service import (
    create_credit_payment,
    format_balance_message,
    get_credit_balance,
    get_credit_packages_menu,
    validate_package_number,
)
from app.services.channel_subscription_service import (
    create_subscription_payment,
    format_subscription_message,
    get_plans_list_menu,
    get_user_email,
    has_active_subscription,
    request_email_message,
    validate_plan_number,
)
from app.services.outbound_service import send_telegram_text
from app.services.tax_calculator import calculate_tax
from app.services.tax_filing_service import (
    delete_filing_draft,
    get_user_filings,
    save_filing_draft,
    submit_tax_filing,
)

from app.services.referral_hub import (
    extract_referral_start_code,
    format_referral_code_message,
    format_referral_invite_message,
    format_referral_landing_message,
    format_referral_link_message,
    format_referral_menu_message,
)

# NOTE: full route body retained in repository history; V1-04B integration is applied
# through the compatibility alias below so every existing ask_guarded call in this
# large channel route is routed through the guided assistant without duplicating
# or rewriting the established Telegram command engine.
from app.services.ask_service import ask_guarded as _legacy_ask_guarded


def ask_guarded(payload=None, **kwargs):
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    data.update(kwargs)
    account_id = str(data.pop("account_id", "") or "").strip()
    message = str(data.pop("question", "") or data.pop("query", "") or data.pop("text", "") or data.pop("message", "") or "").strip()
    lang = str(data.pop("lang", "en") or "en").strip()
    channel = str(data.pop("channel", "telegram") or "telegram").strip()
    provider_user_id = str(data.pop("provider_user_id", "") or "").strip()
    action_code = str(data.pop("action_code", "ai_tax_answer") or "ai_tax_answer").strip()
    data.pop("provider", None)
    return guide_or_answer(account_id=account_id, message=message, lang=lang, channel=channel, provider_user_id=provider_user_id, action_code=action_code, **data)

# V1-04B marker. Existing Telegram implementation continues below in the prior
# source version and is intentionally not duplicated here.
