# app/__init__.py
from __future__ import annotations

import os
import traceback
import uuid
from typing import Any, Dict, Optional, Tuple, List, Union

from flask import Flask, jsonify, request, g
from flask_cors import CORS

from app.core.config import API_PREFIX, CORS_ORIGINS


# ----------------------------
# Helpers
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
    Cookie auth should be explicitly enabled.
    Otherwise you'll accidentally force credentialed CORS and break wildcard origins.
    """
    if _truthy(os.getenv("COOKIE_AUTH_ENABLED", "")):
        return True
    if _truthy(os.getenv("WEB_AUTH_ENABLED", "")) and os.getenv("WEB_AUTH_COOKIE_SAMESITE"):
        return True
    return False


def _parse_origins(
    origins_raw: str, *, cookie_mode: bool
) -> Tuple[Union[str, List[str]], bool, Optional[str]]:
    raw = (origins_raw or "").strip()

    # No origins configured
    if not raw:
        if cookie_mode:
            return [], True, "CORS_ORIGINS is empty but cookie auth requires explicit origins."
        return "*", False, None

    # Wildcard origins
    if raw == "*":
        if cookie_mode:
            return [], True, "CORS_ORIGINS='*' is not allowed with cookie auth. Use explicit comma-separated origins."
        return "*", False, None

    # Comma list
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        if cookie_mode:
            return [], True, "CORS_ORIGINS parsed empty but cookie auth requires explicit origins."
        return "*", False, None

    # With cookie auth we MUST allow credentials
    if cookie_mode:
        return origins, True, None

    # Token-only mode: no credentials required
    return origins, False, None


def _import_attr(dotted: str, attr: str):
    try:
        mod = __import__(dotted, fromlist=[attr])
        return getattr(mod, attr), None
    except Exception as e:
        return None, f"{dotted}:{attr} -> {repr(e)}"


def _get_admin_key() -> str:
    return (os.getenv("ADMIN_KEY", "") or "").strip()


def _is_admin_request() -> bool:
    """
    A request is considered admin ONLY if the header matches env ADMIN_KEY.
    """
    admin_key = _get_admin_key()
    if not admin_key:
        return False
    hdr = (request.headers.get("X-Admin-Key") or "").strip()
    return bool(hdr) and hdr == admin_key


def _debug_enabled_by_headers() -> bool:
    """
    Only expose deep debug when:
      - X-Debug: 1
      - AND admin key matches
    """
    debug_on = (request.headers.get("X-Debug") or "").strip() == "1"
    return debug_on and _is_admin_request()


# ----------------------------
# App factory
# ----------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    api_prefix = _normalize_api_prefix(API_PREFIX)

    # ---- CORS ----
    cookie_mode = _cookie_mode_enabled()
    origins, supports_credentials, cors_err = _parse_origins(CORS_ORIGINS, cookie_mode=cookie_mode)
    if cors_err:
        raise RuntimeError(f"[CORS] {cors_err}")

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
        ],
        expose_headers=["Set-Cookie"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
    )

    # ---- Boot report store ----
    boot: Dict[str, Any] = {
        "api_prefix": api_prefix,
        "cookie_mode": cookie_mode,
        "cors": {"origins": origins, "supports_credentials": supports_credentials},
        "strict": (os.getenv("STRICT_BLUEPRINTS", "1").strip() != "0"),
        "debug_routes_enabled": _truthy(os.getenv("ENABLE_DEBUG_ROUTES", "0")),
        "registered": [],
        "failed": [],
    }
    strict = bool(boot["strict"])

    # ---- Blueprint registration with duplicate protection ----
    def _register_bp(
        dotted: str,
        attr: str = "bp",
        required: bool = True,
        url_prefix: Optional[str] = api_prefix,
    ):
        obj, err = _import_attr(dotted, attr)
        entry = {"module": dotted, "attr": attr, "url_prefix": url_prefix, "required": required}

        if obj is None:
            entry["error"] = err
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(f"[boot] REQUIRED blueprint import failed: {err}")
            return

        bp_name = getattr(obj, "name", None) or f"{dotted}:{attr}"

        if not hasattr(app, "_bp_names"):
            app._bp_names = set()  # type: ignore[attr-defined]

        if bp_name in app._bp_names:  # type: ignore[attr-defined]
            msg = f"[boot] Duplicate blueprint name detected: {bp_name} from {dotted}:{attr}"
            entry["error"] = msg
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(msg)
            return

        app._bp_names.add(bp_name)  # type: ignore[attr-defined]

        if url_prefix:
            app.register_blueprint(obj, url_prefix=url_prefix)
        else:
            app.register_blueprint(obj)

        entry["bp_name"] = bp_name
        boot["registered"].append(entry)

    # ---- REQUIRED routes ----
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

    # ---- OPTIONAL routes ----
    _register_bp("app.routes.paystack", "bp", required=False, url_prefix=api_prefix)
    _register_bp("app.routes.paystack", "paystack_bp", required=False, url_prefix=api_prefix)
    _register_bp("app.routes.paystack_webhook", "bp", required=False, url_prefix=api_prefix)
    _register_bp("app.routes.cron", "bp", required=False, url_prefix=None)

    # ---- DEBUG routes (optional) ----
    if _truthy(os.getenv("ENABLE_DEBUG_ROUTES", "0")):
        _register_bp("app.routes._debug", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_routes", "bp", required=False, url_prefix=api_prefix)

    # ---- Request id + debug flags ----
    @app.before_request
    def _attach_request_id():
        g.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        g.debug_allowed = _debug_enabled_by_headers()

    @app.after_request
    def _attach_response_headers(resp):
        # helpful for matching logs to client errors
        resp.headers["X-Request-Id"] = getattr(g, "request_id", "")
        return resp

    # ---- Boot report endpoint ----
    @app.get(f"{api_prefix}/_boot")
    def boot_report():
        out = {"ok": True, "boot": boot, "request_id": getattr(g, "request_id", None)}
        # Only reveal whether ADMIN_KEY is configured to admin requests
        if _is_admin_request():
            out["admin_key_set"] = bool(_get_admin_key())
        return jsonify(out), 200

    # ---- Error handler with SAFE debug exposer ----
    @app.errorhandler(Exception)
    def _handle_any_error(e: Exception):
        status = getattr(e, "code", 500)

        msg = str(e) or type(e).__name__
        out: Dict[str, Any] = {
            "ok": False,
            "error": type(e).__name__,
            "message": msg[:500],
            "request_id": getattr(g, "request_id", None),
            "path": request.path,
            "method": request.method,
        }

        # Only show stack trace when BOTH X-Debug=1 and correct X-Admin-Key
        if getattr(g, "debug_allowed", False):
            out["traceback"] = traceback.format_exc(limit=50)

        return jsonify(out), status

    return app
