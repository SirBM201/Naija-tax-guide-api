# app/core/auth.py
from __future__ import annotations

import hashlib
import os
import traceback
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

from flask import g, jsonify, request

from app.core.supabase_client import supabase
from app.core.config import WEB_TOKEN_TABLE, WEB_TOKEN_PEPPER, WEB_AUTH_COOKIE_NAME


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _auth_debug_enabled() -> bool:
    return _truthy(os.getenv("AUTH_DEBUG"))


def _dbg(msg: str) -> None:
    if _auth_debug_enabled():
        print(msg, flush=True)


def get_web_token_pepper() -> str:
    return (os.getenv("WEB_TOKEN_PEPPER", WEB_TOKEN_PEPPER) or WEB_TOKEN_PEPPER).strip()


def token_hash(raw_token: str) -> str:
    pepper = get_web_token_pepper()
    return _sha256_hex(f"tok:{raw_token}:{pepper}")


def _cookie_name() -> str:
    return (os.getenv("WEB_AUTH_COOKIE_NAME", WEB_AUTH_COOKIE_NAME) or WEB_AUTH_COOKIE_NAME).strip()


def _get_bearer_token() -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        return None
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _get_cookie_token() -> Optional[str]:
    name = _cookie_name()
    v = request.cookies.get(name)
    v = (v or "").strip()
    return v or None


def auth_debug_snapshot() -> Dict[str, Any]:
    pepper = get_web_token_pepper()
    return {
        "web_token_table": (os.getenv("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE),
        "pepper_len": len(pepper),
        "pepper_prefix_sha256": _sha256_hex(pepper)[:12],
        "cookie_name": _cookie_name(),
        "auth_debug": _auth_debug_enabled(),
    }


def _lookup_session(raw: str, source: str, table: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    th = token_hash(raw)
    th_prefix = th[:12]

    _dbg(f"[auth] lookup src={source} token_hash_prefix={th_prefix} path={request.path} method={request.method}")

    res = (
        _sb()
        .table(table)
        .select("*")
        .eq("token_hash", th)
        .limit(1)
        .execute()
    )
    rows = (res.data or []) if hasattr(res, "data") else []

    if not rows:
        _dbg(f"[auth] invalid_token: src={source} token_hash_prefix={th_prefix} not found in {table}")
        return None, {"error": "invalid_token", "source": source, "token_hash_prefix": th_prefix}

    row = rows[0]

    if row.get("revoked") is True or row.get("revoked_at"):
        _dbg(f"[auth] token_revoked: src={source} token_hash_prefix={th_prefix}")
        return None, {"error": "token_revoked", "source": source, "token_hash_prefix": th_prefix}

    expires_at = row.get("expires_at")
    if expires_at:
        v = str(expires_at).replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(v).astimezone(timezone.utc)
        if _now_utc() > exp_dt:
            _dbg(f"[auth] token_expired: src={source} token_hash_prefix={th_prefix} exp={exp_dt.isoformat()}")
            return None, {
                "error": "token_expired",
                "source": source,
                "token_hash_prefix": th_prefix,
                "expires_at": exp_dt.isoformat(),
            }

    return {
        "raw": raw,
        "source": source,
        "token_hash": th,
        "token_hash_prefix": th_prefix,
        "row": row,
    }, {"ok": True, "source": source, "token_hash_prefix": th_prefix}


def require_auth_plus(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        bearer = _get_bearer_token()
        cookie = _get_cookie_token()

        candidates: list[tuple[str, str]] = []
        if bearer:
            candidates.append(("bearer", bearer))
        if cookie and cookie != bearer:
            candidates.append(("cookie", cookie))

        if not candidates:
            _dbg("[auth] missing_token: neither bearer nor cookie present")
            return jsonify({"ok": False, "error": "missing_token"}), 401

        table = (os.getenv("WEB_TOKEN_TABLE", WEB_TOKEN_TABLE) or WEB_TOKEN_TABLE).strip()
        last_error: Dict[str, Any] = {"error": "invalid_token"}

        try:
            for source, raw in candidates:
                session_info, lookup_result = _lookup_session(raw, source, table)
                if not session_info:
                    last_error = lookup_result
                    continue

                row = session_info["row"]
                th = session_info["token_hash"]

                try:
                    _sb().table(table).update({"last_seen_at": _now_utc().isoformat()}).eq("token_hash", th).execute()
                except Exception as e:
                    _dbg(f"[auth] last_seen_at update skipped: {type(e).__name__}: {str(e)[:160]}")

                g.account_id = row.get("account_id")
                g.web_token_hash = th
                g.raw_token_source = source
                g.token_row = row
                g.auth_token = raw
                g.rotated_token = None

                _dbg(f"[auth] ok account_id={g.account_id} src={source} token_hash_prefix={session_info['token_hash_prefix']}")
                return fn(*args, **kwargs)

            return jsonify({"ok": False, "error": last_error.get("error") or "invalid_token"}), 401

        except Exception as e:
            _dbg(f"[auth] auth_failed: {type(e).__name__}: {str(e)[:220]}")
            _dbg("[auth] traceback:\n" + traceback.format_exc())
            return jsonify({"ok": False, "error": "auth_failed"}), 401

    return wrapper


require_web_auth = require_auth_plus
