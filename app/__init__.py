# app/__init__.py
from __future__ import annotations

import os
import uuid
import importlib
from typing import Any, Dict, Optional, Tuple, List, Union

from flask import Flask, jsonify, request
from flask_cors import CORS

from app.core.config import API_PREFIX, CORS_ORIGINS


# ----------------------------
# Basics
# ----------------------------
def _normalize_api_prefix(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "/api"
    if not v.startswith("/"):
        v = "/" + v
    return v.rstrip("/")


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _cookie_mode_enabled() -> bool:
    """
    Cookie auth mode must be explicitly enabled (or implicitly inferred)
    because it changes CORS behavior (requires explicit origins + credentials).
    """
    if _truthy(os.getenv("COOKIE_AUTH_ENABLED", "")):
        return True
    # Backwards compat: if web auth enabled + cookie samesite is configured,
    # assume cookie mode.
    if _truthy(os.getenv("WEB_AUTH_ENABLED", "")) and (os.getenv("WEB_AUTH_COOKIE_SAMESITE") or "").strip():
        return True
    return False


def _parse_origins(
    origins_raw: str, *, cookie_mode: bool
) -> Tuple[Union[str, List[str]], bool, Optional[str]]:
    raw = (origins_raw or "").strip()

    # If empty: allow "*" ONLY when not using cookies
    if not raw:
        if cookie_mode:
            return [], True, "CORS_ORIGINS is empty but cookie auth requires explicit origins."
        return "*", False, None

    # If wildcard
    if raw == "*":
        if cookie_mode:
            return [], True, "CORS_ORIGINS='*' is not allowed with cookie auth. Use explicit comma-separated origins."
        return "*", False, None

    # Parse list
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        if cookie_mode:
            return [], True, "CORS_ORIGINS parsed empty but cookie auth requires explicit origins."
        return "*", False, None

    if cookie_mode:
        return origins, True, None

    # If not cookie-mode, credentials should be False unless you intentionally want them
    return origins, False, None


# ----------------------------
# Import helpers (root-cause exposer)
# ----------------------------
def _import_attr(dotted: str, attr: str) -> Tuple[Any, Optional[str], Optional[str]]:
    """
    Returns (obj, err, hint)
    """
    try:
        mod = importlib.import_module(dotted)
        if not hasattr(mod, attr):
            # common actionable hint
            hint = None
            if "paystack" in dotted:
                hint = (
                    f"{dotted} does not export `{attr}`. "
                    f"Fix: export `bp = Blueprint(...)` OR call the correct attribute "
                    f"(often `paystack_bp` / `webhook_bp`)."
                )
            else:
                hint = f"{dotted} must export `{attr}` (Blueprint)."
            return None, f"{dotted}:{attr} -> AttributeError(module has no attribute '{attr}')", hint
        return getattr(mod, attr), None, None
    except Exception as e:
        # keep it compact but useful
        hint = None
        if "ImportError" in repr(e) or "ModuleNotFoundError" in repr(e):
            hint = (
                f"Import failed for {dotted}. "
                f"Fix: confirm file path exists and imports inside that module do not crash."
            )
        return None, f"{dotted}:{attr} -> {repr(e)}", hint


def create_app() -> Flask:
    app = Flask(__name__)

    api_prefix = _normalize_api_prefix(API_PREFIX)

    # ----------------------------
    # CORS
    # ----------------------------
    cookie_mode = _cookie_mode_enabled()
    origins, supports_credentials, cors_err = _parse_origins(CORS_ORIGINS, cookie_mode=cookie_mode)
    if cors_err:
        raise RuntimeError(f"[CORS] {cors_err}")

    # Apply CORS to API routes
    CORS(
        app,
        resources={rf"{api_prefix}/*": {"origins": origins}},
        supports_credentials=supports_credentials,
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Auth-Token",
            "X-Requested-With",
            "X-Admin-Key",
            "X-Debug",
            "X-Request-Id",
        ],
        expose_headers=["Set-Cookie", "X-Request-Id"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
    )

    # ----------------------------
    # Request ID + Debug flag
    # ----------------------------
    @app.before_request
    def _assign_request_id():
        rid = (request.headers.get("X-Request-Id") or "").strip()
        if not rid:
            rid = str(uuid.uuid4())
        request.environ["REQUEST_ID"] = rid

    def _rid() -> str:
        return str(request.environ.get("REQUEST_ID") or "")

    def _debug_enabled() -> bool:
        return (request.headers.get("X-Debug") or "").strip() == "1"

    # ----------------------------
    # Boot report (tracks what registered vs failed)
    # ----------------------------
    boot: Dict[str, Any] = {
        "api_prefix": api_prefix,
        "cookie_mode": cookie_mode,
        "cors": {"origins": origins, "supports_credentials": supports_credentials},
        "strict": (os.getenv("STRICT_BLUEPRINTS", "1").strip() != "0"),
        "debug_routes_enabled": _truthy(os.getenv("ENABLE_DEBUG_ROUTES", "0")),
        "registered": [],
        "failed": [],
    }
    strict: bool = bool(boot["strict"])

    # internal set for duplicate bp detection
    if not hasattr(app, "_bp_names"):
        app._bp_names = set()  # type: ignore[attr-defined]

    def _register_bp(
        dotted: str,
        attr: str = "bp",
        *,
        required: bool = True,
        url_prefix: Optional[str] = api_prefix,
        alias_name: Optional[str] = None,
    ) -> bool:
        """
        Returns True if registered, False otherwise.
        """
        obj, err, hint = _import_attr(dotted, attr)
        entry: Dict[str, Any] = {
            "module": dotted,
            "attr": attr,
            "url_prefix": url_prefix,
            "required": required,
        }
        if alias_name:
            entry["alias_name"] = alias_name

        if obj is None:
            entry["error"] = err
            if hint:
                entry["hint"] = hint
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(f"[boot] REQUIRED blueprint import failed: {err}")
            return False

        # blueprint name used by Flask
        bp_name = getattr(obj, "name", None) or (alias_name or f"{dotted}:{attr}")

        # duplicate detection
        if bp_name in app._bp_names:  # type: ignore[attr-defined]
            msg = f"[boot] Duplicate blueprint name detected: {bp_name} from {dotted}:{attr}"
            entry["error"] = msg
            entry["hint"] = (
                "Fix: ensure each Blueprint(name, __name__) has a unique name, "
                "or remove duplicate registration attempts."
            )
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(msg)
            return False

        app._bp_names.add(bp_name)  # type: ignore[attr-defined]

        # register
        if url_prefix is not None:
            app.register_blueprint(obj, url_prefix=url_prefix)
        else:
            app.register_blueprint(obj)

        entry["bp_name"] = bp_name
        boot["registered"].append(entry)
        return True

    # ----------------------------
    # REQUIRED routes
    # ----------------------------
    required_modules = [
        "app.routes.health",
        "app.routes.accounts",
        "app.routes.subscriptions",
        "app.routes.ask",
        "app.routes.webhooks",
        "app.routes.plans",
        "app.routes.link_tokens",
        "app.routes.admin_link_tokens",
        "app.routes.accounts_admin",
        "app.routes.meta",
        "app.routes.email_link",
        "app.routes.web_auth",
        "app.routes.web_session",
    ]
    for dotted in required_modules:
        _register_bp(dotted, "bp", required=True, url_prefix=api_prefix)

    # ----------------------------
    # OPTIONAL routes (Paystack + Cron + Debug)
    # ----------------------------

    # Paystack: handle drift safely by trying multiple known blueprint symbols.
    # This prevents your boot report from showing scary errors if your file exports a different name.
    paystack_candidates = [
        ("app.routes.paystack", "bp", "paystack"),
        ("app.routes.paystack", "paystack_bp", "paystack"),
        ("app.routes.paystack", "webhook_bp", "paystack_webhook"),
        ("app.routes.paystack_webhook", "bp", "paystack_webhook"),
        ("app.routes.paystack_webhooks", "bp", "paystack_webhooks"),
    ]
    # Try each until at least one registers (but don’t fail boot if none exist).
    any_paystack = False
    for mod, attr, alias in paystack_candidates:
        ok = _register_bp(mod, attr, required=False, url_prefix=api_prefix, alias_name=alias)
        any_paystack = any_paystack or ok

    # Cron MUST be under /api so PowerShell matches:
    #   $Base/api/internal/cron/expire-subscriptions
    _register_bp("app.routes.cron", "bp", required=False, url_prefix=api_prefix)

    # Debug routes (optional)
    if _truthy(os.getenv("ENABLE_DEBUG_ROUTES", "0")):
        _register_bp("app.routes._debug", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_routes", "bp", required=False, url_prefix=api_prefix)

    # ----------------------------
    # Diagnostics endpoints
    # ----------------------------
    @app.get(f"{api_prefix}/_boot")
    def boot_report():
        """
        Single source-of-truth startup status:
        - which blueprints registered
        - which failed and WHY
        - hints on how to fix
        """
        admin_key_set = bool((os.getenv("ADMIN_KEY") or "").strip())
        out = {
            "ok": True,
            "request_id": _rid(),
            "admin_key_set": admin_key_set,
            "boot": boot,
        }
        return jsonify(out), 200

    @app.get(f"{api_prefix}/_diag")
    def diag():
        """
        Lightweight runtime sanity page for quick root-cause checks.
        Safe to expose (does NOT leak secrets).
        """
        env = {
            "API_PREFIX": api_prefix,
            "COOKIE_MODE": cookie_mode,
            "CORS_ORIGINS_MODE": "*" if origins == "*" else "list",
            "SUPPORTS_CREDENTIALS": supports_credentials,
            "ENABLE_DEBUG_ROUTES": _truthy(os.getenv("ENABLE_DEBUG_ROUTES", "0")),
            "STRICT_BLUEPRINTS": strict,
            "ADMIN_KEY_SET": bool((os.getenv("ADMIN_KEY") or "").strip()),
            "WEB_AUTH_ENABLED": _truthy(os.getenv("WEB_AUTH_ENABLED", "")),
        }

        # quick hints if misconfigured
        hints: List[str] = []
        if cookie_mode and (origins == "*" or origins == []):
            hints.append(
                "Cookie mode is ON but CORS origins are not explicit. "
                "Fix: set CORS_ORIGINS to comma-separated origins (no '*')."
            )
        if not env["ADMIN_KEY_SET"]:
            hints.append("ADMIN_KEY is not set. Internal cron/admin endpoints will be blocked.")

        return jsonify({"ok": True, "request_id": _rid(), "env": env, "hints": hints}), 200

    # ----------------------------
    # Global error handler (root cause exposure)
    # ----------------------------
    @app.errorhandler(Exception)
    def _handle_any_error(e: Exception):
        status = getattr(e, "code", 500)
        msg = str(e) or type(e).__name__

        out: Dict[str, Any] = {
            "ok": False,
            "request_id": _rid(),
            "error": type(e).__name__,
            "message": msg[:800],
        }

        # Simple “way out” suggestions for common failures
        suggestions: List[str] = []
        if "CORS" in msg or "cors" in msg.lower():
            suggestions.append(
                "CORS failure: if COOKIE_MODE is enabled, set explicit CORS_ORIGINS "
                "(comma-separated) and avoid '*'."
            )
        if "ADMIN_KEY" in msg or "admin" in msg.lower():
            suggestions.append("Admin/auth failure: confirm ADMIN_KEY is set in Koyeb env vars.")
        if "Duplicate blueprint name" in msg:
            suggestions.append("Blueprint collision: ensure unique Blueprint(name, __name__) values.")
        if suggestions:
            out["suggestions"] = suggestions

        if _debug_enabled():
            import traceback as _tb

            out["debug"] = {
                "path": request.path,
                "method": request.method,
                "content_type": request.content_type,
                "args": dict(request.args or {}),
            }
            out["traceback"] = _tb.format_exc(limit=50)

        return jsonify(out), status

    return app
