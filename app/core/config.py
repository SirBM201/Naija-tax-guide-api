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
        return default


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
# Web Auth / Web Sessions
# -----------------------------
WEB_AUTH_ENABLED = env_bool("WEB_AUTH_ENABLED", True)

# Hashing peppers (MUST be set in prod)
WEB_TOKEN_PEPPER = env("WEB_TOKEN_PEPPER", "dev-pepper-change-me")
WEB_OTP_PEPPER = env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)

# ✅ IMPORTANT exports (your boot error was missing WEB_TOKEN_TABLE)
# default to the newer table names you are using now
WEB_TOKEN_TABLE = env("WEB_TOKEN_TABLE", "web_sessions")  # "web_sessions" preferred
WEB_OTP_TABLE = env("WEB_OTP_TABLE", "web_otps")          # "web_otps"

# OTP lifetime
WEB_OTP_TTL_SECONDS = env_int("WEB_OTP_TTL_SECONDS", 600)  # 10 mins
WEB_OTP_TTL_MINUTES = env_int("WEB_OTP_TTL_MINUTES", max(1, WEB_OTP_TTL_SECONDS // 60))
WEB_OTP_MAX_ATTEMPTS = env_int("WEB_OTP_MAX_ATTEMPTS", 5)

# Session lifetime
WEB_SESSION_TTL_DAYS = env_int("WEB_SESSION_TTL_DAYS", 30)

# Cookie (canonical names)
WEB_AUTH_COOKIE_NAME = env("WEB_AUTH_COOKIE_NAME", "ntg_session")
WEB_AUTH_COOKIE_SECURE = env_bool("WEB_AUTH_COOKIE_SECURE", True)
WEB_AUTH_COOKIE_SAMESITE = env("WEB_AUTH_COOKIE_SAMESITE", "None")  # cross-site needs None
WEB_AUTH_COOKIE_DOMAIN = env("WEB_AUTH_COOKIE_DOMAIN", "")          # usually blank
WEB_AUTH_COOKIE_MAX_AGE = env_int("WEB_AUTH_COOKIE_MAX_AGE", 2592000)  # 30 days

# Debug
WEB_AUTH_DEBUG = env_bool("WEB_AUTH_DEBUG", False)
WEB_DEV_RETURN_OTP = env_bool("WEB_DEV_RETURN_OTP", False) or (ENV.lower() == "dev")

# ---------------------------------------------------------
# Backwards-compatible ENV aliases (so older code won’t break)
# ---------------------------------------------------------
# Your web_auth.py route currently reads COOKIE_* env vars directly.
# We keep these aliases so you can set either style in Koyeb.
COOKIE_AUTH_ENABLED = env_bool("COOKIE_AUTH_ENABLED", True)
COOKIE_SECURE = env_bool("COOKIE_SECURE", WEB_AUTH_COOKIE_SECURE)
COOKIE_SAMESITE = env("COOKIE_SAMESITE", WEB_AUTH_COOKIE_SAMESITE)
COOKIE_DOMAIN = env("COOKIE_DOMAIN", WEB_AUTH_COOKIE_DOMAIN)
COOKIE_MAX_AGE = env_int("COOKIE_MAX_AGE", WEB_AUTH_COOKIE_MAX_AGE)

WEB_AUTH_RETURN_BEARER = env_bool("WEB_AUTH_RETURN_BEARER", False)
WEB_OTP_RETURN_PLAIN = env_bool("WEB_OTP_RETURN_PLAIN", False)


# -----------------------------
# Dev bypass controls (auth + subscription)
# -----------------------------
ALLOW_DEV_BYPASS = env_bool("ALLOW_DEV_BYPASS", True)
DEV_BYPASS_SUBSCRIPTION = env_bool("DEV_BYPASS_SUBSCRIPTION", False)


# -----------------------------
# Paystack
# -----------------------------
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_CURRENCY = env("PAYSTACK_CURRENCY", "NGN") or "NGN"
PAYSTACK_CALLBACK_URL = env("PAYSTACK_CALLBACK_URL", "")
PAYSTACK_WEBHOOK_TOLERANCE_SECONDS = env_int("PAYSTACK_WEBHOOK_TOLERANCE_SECONDS", 300)
