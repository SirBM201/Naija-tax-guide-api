# app/core/auth.py
from __future__ import annotations

import hashlib
import os
import traceback
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional, Dict, Tuple

from flask import g, jsonify, request

from app.core.supabase_client import supabase
from app.core.config import WEB_TOKEN_TABLE, WEB_TOKEN_PEPPER

from app.services.web_tokens_service import (
    extract_any_token,
    validate_token,
    touch_last_seen,
)


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _auth_debug_enabled() -> bool:
    return _truthy(os.getenv("AUTH_DEBUG"))


def _dbg(msg: str) -> None:
    if _auth_debug_enabled():
        print(msg, flush=True)


def get_web_token_pepper() -> str:
    return (_env("WEB_TOKEN_PEPPER", WEB_TOKEN_PEPPER) or WEB_TOKEN_PEPPER).strip()


def token_hash(raw_token: str) -> str:
    """
    Single source of truth for token hashing.
    (pepper + raw_token) -> sha256 hex
    """
    pepper = get_web_token_pepper()
    return _sha256_hex(f"{pepper}:{raw_token}")


def auth_debug_snapshot() -> Dict[str, Any]:
    pepper = get_web_token_pepper()
    return {
        "web_token_table": (_env("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE),
        "pepper_len": len(pepper),
        "pepper_prefix_sha256": _sha256_hex(pepper)[:12],
        "cookie_name": _env("WEB_AUTH_COOKIE_NAME", "ntg_session"),
        "auth_debug": _auth_debug_enabled(),
    }


def _touch_last_seen_best_effort(table: str, raw_token: str) -> None:
    """
    Touch last_seen_at only if column exists; never raises.
    We do a quick best-effort probe to avoid breaking auth on schema mismatch.
    """
    if not raw_token:
        return
    try:
        # If table/column doesn't exist, touch_last_seen already bails out.
        touch_last_seen(raw_token, table=table)
    except Exception:
        return


def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Cookie-first auth, Bearer fallback (via extract_any_token()).

    Sets (stable contract for routes/middleware):
      g.account_id
      g.token_row
      g.auth_token         (raw token)
      g.auth_source        ("cookie" | "bearer")
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw, source = extract_any_token(request)
        if not raw:
            _dbg("[auth] missing_token: neither cookie nor bearer present")
            return jsonify({"ok": False, "error": "missing_token"}), 401

        table = (_env("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE).strip()
        th_prefix = token_hash(raw)[:12]

        try:
            _dbg(f"[auth] start src={source} token_hash_prefix={th_prefix} path={request.path} method={request.method}")

            ok, payload, err = validate_token(raw, table=table, touch=False)
            if not ok:
                _dbg(f"[auth] reject src={source} token_hash_prefix={th_prefix} err={err}")
                return jsonify({"ok": False, "error": err or "unauthorized"}), 401

            account_id = (payload.get("account_id") or "").strip()
            token_row = payload.get("token_row") or {}

            if not account_id:
                _dbg(f"[auth] invalid_token: missing account_id token_hash_prefix={th_prefix}")
                return jsonify({"ok": False, "error": "invalid_token"}), 401

            # Best-effort last_seen_at touch
            _touch_last_seen_best_effort(table, raw)

            # Populate g for downstream routes
            g.account_id = account_id
            g.token_row = token_row
            g.auth_token = raw
            g.auth_source = source or "bearer"

            _dbg(f"[auth] ok account_id={g.account_id} src={g.auth_source} token_hash_prefix={th_prefix}")
            return fn(*args, **kwargs)

        except Exception as e:
            _dbg(f"[auth] auth_failed: {type(e).__name__}: {str(e)[:220]}")
            _dbg("[auth] traceback:\n" + traceback.format_exc())
            # keep response stable
            return jsonify({"ok": False, "error": "auth_failed"}), 401

    return wrapper
