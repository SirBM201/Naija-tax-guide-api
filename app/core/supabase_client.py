# app/core/supabase_client.py
from __future__ import annotations

import os
from typing import Optional

from supabase import create_client
from supabase.client import Client


# Lazy singletons
_client_admin: Optional[Client] = None
_client_anon: Optional[Client] = None


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get_supabase_url() -> str:
    url = _env("SUPABASE_URL") or _env("NEXT_PUBLIC_SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL is missing")
    return url


def _get_service_key() -> str:
    """
    Backend/admin Supabase key.

    Preferred:
    - SUPABASE_SERVICE_ROLE_KEY

    Backward-compatible fallbacks:
    - SUPABASE_SERVICE_KEY
    - SERVICE_ROLE_KEY
    """
    return (
        _env("SUPABASE_SERVICE_ROLE_KEY")
        or _env("SUPABASE_SERVICE_KEY")
        or _env("SERVICE_ROLE_KEY")
    )


def _get_anon_key() -> str:
    return _env("SUPABASE_ANON_KEY") or _env("NEXT_PUBLIC_SUPABASE_ANON_KEY")


def get_supabase_client(admin: bool = True) -> Client:
    """
    Canonical Supabase getter used throughout the backend.

    admin=True:
        Uses service-role key when available.
        Recommended for backend writes and server-side operations.

    admin=False:
        Uses anon key.
        Rarely needed in backend routes.
    """
    global _client_admin, _client_anon

    url = _get_supabase_url()

    if admin:
        if _client_admin is not None:
            return _client_admin

        key = _get_service_key() or _get_anon_key()
        if not key:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is missing"
            )

        _client_admin = create_client(url, key)
        return _client_admin

    if _client_anon is not None:
        return _client_anon

    anon_key = _get_anon_key()
    if not anon_key:
        raise RuntimeError("SUPABASE_ANON_KEY is missing")

    _client_anon = create_client(url, anon_key)
    return _client_anon


# -------------------------------------------------------------------
# Backward-compatible exports
# -------------------------------------------------------------------
# Different routes in this project currently import this client using
# different names. Keep all aliases so old and new files can boot safely.
# -------------------------------------------------------------------

# Main backend admin client
supabase: Client = get_supabase_client(admin=True)

# IMPORTANT:
# Some routes, especially billing.py, import this exact name:
# from app.core.supabase_client import supabase_client
supabase_client: Client = supabase

# Extra compatibility aliases
client: Client = supabase
db: Client = supabase


def get_supabase() -> Client:
    return get_supabase_client(admin=True)


def supabase_admin() -> Client:
    return get_supabase_client(admin=True)


def supabase_anon() -> Client:
    return get_supabase_client(admin=False)
