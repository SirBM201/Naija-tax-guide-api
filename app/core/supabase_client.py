# app/core/supabase_client.py
from __future__ import annotations

import os
from typing import Optional, Any

from supabase import create_client
from supabase.client import Client


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _require(name: str) -> str:
    v = _env(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


class SupabaseProxy:
    """
    Backward compatible proxy.

    Supports BOTH styles:
      - supabase().rpc(...)
      - supabase.rpc(...)

    This prevents the exact error you hit:
      TypeError: 'SyncClient' object is not callable
    """

    def __init__(self) -> None:
        self._client: Optional[Client] = None

    def _build_client(self) -> Client:
        url = _require("SUPABASE_URL")

        # Prefer service role on the backend
        key = _env("SUPABASE_SERVICE_ROLE_KEY") or _env("SUPABASE_SERVICE_KEY") or _env("SUPABASE_ANON_KEY")
        if not key:
            raise RuntimeError(
                "Missing Supabase key. Set one of: SUPABASE_SERVICE_ROLE_KEY, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY"
            )

        return create_client(url, key)

    def get(self) -> Client:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def reset(self) -> None:
        self._client = None

    def __call__(self) -> Client:
        # Allows: supabase().rpc(...)
        return self.get()

    def __getattr__(self, name: str) -> Any:
        # Allows: supabase.rpc(...)
        return getattr(self.get(), name)


# Exported symbol used across the app
supabase = SupabaseProxy()
