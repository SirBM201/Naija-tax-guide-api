# app/core/config.py
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    v = env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    v = env(name, str(default))
    try:
        return int(v)
    except Exception:
        return int(default)


# -----------------------------
# Core / Runtime
# -----------------------------
ENV = env("ENV", "prod").lower()
DEBUG = env_bool("DEBUG", False) or (ENV == "dev")

PORT = env_int("PORT", 8000)

# Routing
API_PREFIX = env("API_PREFIX", "")  # "" or "/api"
if API_PREFIX and not API_PREFIX.startswith("/"):
    API_PREFIX = "/" + API_PREFIX
API_PREFIX = API_PREFIX.rstrip("/")

# CORS
# Comma-separated or "*" (if you use cookies cross-site, you must NOT use "*")
CORS_ORIGINS = env("CORS_ORIGINS", "*")


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
# Web Auth / Web Sessions (matches your current web_auth_service.py)
# -----------------------------
WEB_AUTH_ENABLED = env_bool("WEB_AUTH_ENABLED", True)

# Hashing peppers
WEB_TOKEN_PEPPER = env("WEB_TOKEN_PEPPER", "dev-pepper-change-me")
WEB_OTP_PEPPER = env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)

# IMPORTANT: your current service uses these actual tables
WEB_TOKEN_TABLE = env("WEB_TOKEN_TABLE", "web_tokens")
WEB_OTP_TABLE = env("WEB_OTP_TABLE", "web_otps")

# OTP lifetime
WEB_OTP_TTL_MINUTES = env_int("WEB_OTP_TTL_MINUTES", 10)
WEB_OTP_TTL_SECONDS = env_int("WEB_OTP_TTL_SECONDS", WEB_OTP_TTL_MINUTES * 60)
WEB_OTP_MAX_ATTEMPTS = env_int("WEB_OTP_MAX_ATTEMPTS", 5)

# Token/session lifetime
WEB_TOKEN_TTL_DAYS = env_int("WEB_TOKEN_TTL_DAYS", 30)

# Cookie settings (used by routes/web_auth.py)
WEB_AUTH_COOKIE_NAME = env("WEB_AUTH_COOKIE_NAME", "ntg_web_token")
WEB_AUTH_COOKIE_SECURE = env_bool("WEB_AUTH_COOKIE_SECURE", True)
WEB_AUTH_COOKIE_SAMESITE = env("WEB_AUTH_COOKIE_SAMESITE", "None")  # None for cross-site cookie
WEB_AUTH_COOKIE_DOMAIN = env("WEB_AUTH_COOKIE_DOMAIN", "")  # keep blank normally
WEB_AUTH_COOKIE_MAX_AGE = env_int("WEB_AUTH_COOKIE_MAX_AGE", 2592000)  # 30 days

# Route behavior toggles
COOKIE_AUTH_ENABLED = env_bool("COOKIE_AUTH_ENABLED", True)  # legacy supported by your routes
WEB_AUTH_RETURN_BEARER = env_bool("WEB_AUTH_RETURN_BEARER", False)
WEB_OTP_RETURN_PLAIN = env_bool("WEB_OTP_RETURN_PLAIN", False)  # DEV ONLY


# -----------------------------
# Paystack
# -----------------------------
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_CURRENCY = env("PAYSTACK_CURRENCY", "NGN") or "NGN"
PAYSTACK_CALLBACK_URL = env("PAYSTACK_CALLBACK_URL", "")
PAYSTACK_WEBHOOK_TOLERANCE_SECONDS = env_int("PAYSTACK_WEBHOOK_TOLERANCE_SECONDS", 300)


# -----------------------------
# Dev bypass flags (support BOTH naming styles you currently have)
# -----------------------------
# UI env you showed:
BYPASS_AUTH = env_bool("BYPASS_AUTH", False)  # you set DISABLED -> effectively False
DEV_BYPASS_SUBSCRIPTION = env_bool("DEV_BYPASS_SUBSCRIPTION", False)

# Code env used in ask.py right now:
BYPASS_TOKEN = env("BYPASS_TOKEN", "")
DEV_BYPASS_TOKEN = env("DEV_BYPASS_TOKEN", "")

# Unified meaning for "subscription bypass is allowed"
# If you want bypass OFF completely, set:
#   DEV_BYPASS_SUBSCRIPTION=0 and remove BYPASS_TOKEN/DEV_BYPASS_TOKEN
ALLOW_SUBSCRIPTION_BYPASS = DEV_BYPASS_SUBSCRIPTION or bool(BYPASS_TOKEN or DEV_BYPASS_TOKEN)
