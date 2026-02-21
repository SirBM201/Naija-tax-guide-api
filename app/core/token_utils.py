# app/core/token_utils.py
from __future__ import annotations

import hashlib
import os


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def get_web_token_pepper(fallback: str = "dev-pepper-change-me") -> str:
    """
    Single source of truth:
      - WEB_TOKEN_PEPPER (preferred)
      - fallback passed by caller (usually config.WEB_TOKEN_PEPPER)
    """
    return (_env("WEB_TOKEN_PEPPER") or fallback).strip()


def token_hash(raw_token: str, fallback_pepper: str = "dev-pepper-change-me") -> str:
    """
    Hash token using: sha256(pepper:raw_token)
    Kept dependency-free to avoid circular imports.
    """
    pepper = get_web_token_pepper(fallback=fallback_pepper)
    raw_token = (raw_token or "").strip()
    return _sha256_hex(f"{pepper}:{raw_token}")
