# app/core/token_utils.py
from __future__ import annotations

import hashlib
import os


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def token_hash(raw_token: str, fallback_pepper: str = "") -> str:
    """
    Hash web session tokens using a pepper.
    This module must stay dependency-light (NO Flask imports),
    so it can be imported anywhere without causing circular imports.
    """
    raw_token = (raw_token or "").strip()
    pepper = (os.getenv("WEB_TOKEN_PEPPER", fallback_pepper) or fallback_pepper).strip()
    return _sha256_hex(f"{pepper}:{raw_token}")
