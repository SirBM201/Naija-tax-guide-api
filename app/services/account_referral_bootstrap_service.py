from __future__ import annotations

from typing import Dict

from app.services.referral_service import bootstrap_new_account_for_referrals


def bootstrap_account_referral_state(
    *,
    account_id: str,
    referral_code: str | None = None,
    source: str = "signup",
) -> Dict[str, object]:
    """
    Call this immediately after you create or resolve a new account.

    Example use cases:
    - after OTP verification creates a first-time account
    - after registration creates the account row
    - after social login creates a new account row

    `referral_code` should be the incoming code captured from:
    - query param ?ref=XXXX
    - signup form hidden field
    - cookie/local storage forwarded by frontend
    """
    result = bootstrap_new_account_for_referrals(
        account_id=account_id,
        incoming_referral_code=referral_code,
        source=source,
    )

    return {
        "ok": True,
        "account_id": result.account_id,
        "own_profile": result.own_profile,
        "captured_referral": result.captured_referral,
        "skipped_reason": result.skipped_reason,
    }

