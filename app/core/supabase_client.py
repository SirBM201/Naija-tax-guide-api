# app/core/supabase_client.py
"""
Central Supabase client factory for the backend.

Rules:
- Backend ALWAYS uses SERVICE ROLE key
- Client is created once (singleton)
- Codebase uses supabase() as a callable everywhere
"""

from __future__ import annotations

from typing import Any, Optional

from supabase import create_client

from .config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

_client: Optional[Any] = None


def supabase() -> Any:
    """
    Returns a singleton Supabase client using SERVICE ROLE key.
    Server-side only. Never expose to frontend.
    """
    global _client

    if _client is not None:
        return _client

    url = (SUPABASE_URL or "").strip()
    key = (SUPABASE_SERVICE_ROLE_KEY or "").strip()

    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")

    _client = create_client(url, key)
    return _client
