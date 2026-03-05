# app/core/config.py
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    v = env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "y", "on")


# -----------------------------
# Core
# -----------------------------
ENV = env("ENV", "prod")
PORT = int(env("PORT", "8000") or "8000")

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

# Token hashing peppers
WEB_TOKEN_PEPPER = env("WEB_TOKEN_PEPPER", "dev-pepper-change-me")
WEB_OTP_PEPPER = env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)

# OTP lifetime
WEB_OTP_TTL_MINUTES = int(env("WEB_OTP_TTL_MINUTES", "10") or "10")

# Session lifetime
WEB_TOKEN_TTL_DAYS = int(env("WEB_TOKEN_TTL_DAYS", "30") or "30")

# Cookie settings (your routes/web_auth.py reads COOKIE_* names)
COOKIE_AUTH_ENABLED = env_bool("COOKIE_AUTH_ENABLED", True)
COOKIE_SECURE = env_bool("COOKIE_SECURE", True)
COOKIE_SAMESITE = env("COOKIE_SAMESITE", "None")  # "None" for cross-site Vercel->Koyeb
COOKIE_DOMAIN = env("COOKIE_DOMAIN", "")
COOKIE_MAX_AGE = int(env("COOKIE_MAX_AGE", "2592000") or "2592000")  # 30 days
WEB_AUTH_COOKIE_NAME = env("WEB_AUTH_COOKIE_NAME", "ntg_web_token")

# Token return in JSON
WEB_AUTH_RETURN_BEARER = env_bool("WEB_AUTH_RETURN_BEARER", False)

# Debug
WEB_AUTH_DEBUG = env_bool("WEB_AUTH_DEBUG", False)
ASK_DEBUG = env_bool("ASK_DEBUG", False) or env_bool("DEBUG", False)

# -----------------------------
# Dev bypass controls (Ask)
# -----------------------------
ALLOW_DEV_BYPASS = env_bool("ALLOW_DEV_BYPASS", False)
BYPASS_TOKEN = env("BYPASS_TOKEN", "") or env("DEV_BYPASS_TOKEN", "")

# Optional frontend feature flag (doesn't affect backend unless you also send bypass token)
DEV_BYPASS_SUBSCRIPTION = env_bool("DEV_BYPASS_SUBSCRIPTION", False)


# -----------------------------
# Paystack
# -----------------------------
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_CURRENCY = env("PAYSTACK_CURRENCY", "NGN") or "NGN"
PAYSTACK_CALLBACK_URL = env("PAYSTACK_CALLBACK_URL", "")
PAYSTACK_WEBHOOK_TOLERANCE_SECONDS = int(env("PAYSTACK_WEBHOOK_TOLERANCE_SECONDS", "300") or "300")
