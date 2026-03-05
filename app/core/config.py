# app/core/config.py
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    v = env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)) or str(default))
    except Exception:
        return int(default)


# -----------------------------
# Core
# -----------------------------
ENV = env("ENV", "prod")
PORT = env_int("PORT", 8000)

# Routing
API_PREFIX = env("API_PREFIX", "")  # "" or "/api"
if API_PREFIX and not API_PREFIX.startswith("/"):
    API_PREFIX = "/" + API_PREFIX
API_PREFIX = API_PREFIX.rstrip("/")

# CORS
CORS_ORIGINS = env("CORS_ORIGINS", "*")  # comma-separated or "*"


# -----------------------------
# Supabase
# -----------------------------
SUPABASE_URL = env("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY") or env("SUPABASE_SERVICE_KEY")


# -----------------------------
# AI / OpenAI
# -----------------------------
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")


# -----------------------------
# Admin API protection
# -----------------------------
ADMIN_API_KEY = env("ADMIN_API_KEY", "")


# -----------------------------
# Ask / Debug / Bypass
# -----------------------------
ASK_DEBUG = env_bool("ASK_DEBUG", False) or env_bool("DEBUG", False)

# Dev bypass controls used across ask/web auth
ALLOW_DEV_BYPASS = env_bool("ALLOW_DEV_BYPASS", True)
BYPASS_TOKEN = env("BYPASS_TOKEN", "") or env("DEV_BYPASS_TOKEN", "")
DEV_BYPASS_SUBSCRIPTION = env_bool("DEV_BYPASS_SUBSCRIPTION", False)  # you showed this exists in env
BYPASS_AUTH = env_bool("BYPASS_AUTH", False)  # optional legacy flag


# -----------------------------
# Web Auth / Web Sessions
# -----------------------------
WEB_AUTH_ENABLED = env_bool("WEB_AUTH_ENABLED", True)

# Peppers
WEB_TOKEN_PEPPER = env("WEB_TOKEN_PEPPER", "dev-pepper-change-me")
WEB_OTP_PEPPER = env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)

# Table names (IMPORTANT: export compat aliases so old imports never crash)
# You can set WEB_TOKEN_TABLE in env to either "web_tokens" or "web_sessions" depending on your schema.
WEB_TOKEN_TABLE = env("WEB_TOKEN_TABLE", "web_tokens")  # <--- default to web_tokens (your current web_auth_service uses web_tokens)
WEB_SESSIONS_TABLE = env("WEB_SESSIONS_TABLE", WEB_TOKEN_TABLE)  # compat alias
WEB_TOKENS_TABLE = env("WEB_TOKENS_TABLE", WEB_TOKEN_TABLE)  # compat alias

WEB_OTP_TABLE = env("WEB_OTP_TABLE", "web_otps")

# OTP lifetime (seconds + minutes for compatibility)
WEB_OTP_TTL_SECONDS = env_int("WEB_OTP_TTL_SECONDS", 600)  # 10 mins
WEB_OTP_TTL_MINUTES = env_int("WEB_OTP_TTL_MINUTES", max(1, WEB_OTP_TTL_SECONDS // 60))
WEB_OTP_MAX_ATTEMPTS = env_int("WEB_OTP_MAX_ATTEMPTS", 5)

# Session lifetime
WEB_SESSION_TTL_DAYS = env_int("WEB_SESSION_TTL_DAYS", 30)
WEB_TOKEN_TTL_DAYS = env_int("WEB_TOKEN_TTL_DAYS", WEB_SESSION_TTL_DAYS)  # legacy alias some code uses

# Cookie settings
WEB_AUTH_COOKIE_NAME = env("WEB_AUTH_COOKIE_NAME", "ntg_session")
WEB_AUTH_COOKIE_SECURE = env_bool("WEB_AUTH_COOKIE_SECURE", True)
WEB_AUTH_COOKIE_SAMESITE = env("WEB_AUTH_COOKIE_SAMESITE", "None")  # cross-site (Vercel -> Koyeb)
WEB_AUTH_COOKIE_DOMAIN = env("WEB_AUTH_COOKIE_DOMAIN", "")  # usually blank

# Debug flags
WEB_AUTH_DEBUG = env_bool("WEB_AUTH_DEBUG", False)
WEB_DEV_RETURN_OTP = env_bool("WEB_DEV_RETURN_OTP", False) or (ENV.lower() == "dev")

# Token insertion retry (used by some implementations)
WEB_TOKEN_INSERT_MAX_RETRIES = env_int("WEB_TOKEN_INSERT_MAX_RETRIES", 5)
WEB_TOKEN_INSERT_RETRY_SLEEP_MS = env_int("WEB_TOKEN_INSERT_RETRY_SLEEP_MS", 50)


# -----------------------------
# Paystack
# -----------------------------
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_CURRENCY = env("PAYSTACK_CURRENCY", "NGN") or "NGN"
PAYSTACK_CALLBACK_URL = env("PAYSTACK_CALLBACK_URL", "")
PAYSTACK_WEBHOOK_TOLERANCE_SECONDS = env_int("PAYSTACK_WEBHOOK_TOLERANCE_SECONDS", 300)
