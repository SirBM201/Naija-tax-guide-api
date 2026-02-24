# app/core/supabase_client.py
from __future__ import annotations

import os
from supabase import create_client, Client

_SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()
# IMPORTANT: backend MUST use SERVICE ROLE key for admin writes (never expose to frontend)
_SUPABASE_SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase

    if _supabase is not None:
        return _supabase

    if not _SUPABASE_URL or not _SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

    _supabase = create_client(_SUPABASE_URL, _SUPABASE_SERVICE_ROLE_KEY)
    return _supabase


# Backward compatible export for code that does: `from ... import supabase`
# ✅ This is a CLIENT OBJECT (not a function)
supabase: Client = get_supabase()
