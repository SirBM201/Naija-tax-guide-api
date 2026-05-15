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
    return (
        _env("SUPABASE_SERVICE_ROLE_KEY")
        or _env("SUPABASE_SERVICE_KEY")
        or _env("SERVICE_ROLE_KEY")
    )


def _get_anon_key() -> str:
    return _env("SUPABASE_ANON_KEY") or _env("NEXT_PUBLIC_SUPABASE_ANON_KEY")


def get_supabase_client(admin: bool = True) -> Client:
    """
    Canonical getter used throughout the backend.

    - admin=True: uses SUPABASE_SERVICE_ROLE_KEY (recommended for backend)
    - admin=False: uses SUPABASE_ANON_KEY (rarely needed in backend)
    """
    global _client_admin, _client_anon

    url = _get_supabase_url()

    if admin:
        if _client_admin is not None:
            return _client_admin
        key = _get_service_key() or _get_anon_key()
        if not key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY fallback) is missing")
        _client_admin = create_client(url, key)
        return _client_admin

    if _client_anon is not None:
        return _client_anon
    anon = _get_anon_key()
    if not anon:
        raise RuntimeError("SUPABASE_ANON_KEY is missing")
    _client_anon = create_client(url, anon)
    return _client_anon


# Create the client instance
_supabase_instance: Client = get_supabase_client(admin=True)

# Make supabase callable for compatibility with blueprints that expect a function
def supabase() -> Client:
    """Returns the Supabase client instance (callable for blueprint compatibility)"""
    return _supabase_instance


# Also export as a client for direct use
supabase_client = _supabase_instance

# Backwards compatibility - supabase can be used both as callable and directly
# But to avoid confusion, we'll export both
