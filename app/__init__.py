# app/__init__.py
from __future__ import annotations

import os
import uuid
import warnings
from typing import Any, Dict, Optional, Tuple, List, Union

from flask import Flask, jsonify, request
from flask_cors import CORS

from app.core.config import API_PREFIX, CORS_ORIGINS


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
    if _truthy(os.getenv("COOKIE_AUTH_ENABLED", "1")):
        return True
    if _truthy(os.getenv("WEB_AUTH_ENABLED", "")) and (os.getenv("COOKIE_SAMESITE") or "").strip():
        return True
    return False


def _parse_origins(
    origins_raw: str, *, cookie_mode: bool
) -> Tuple[Union[str, List[str]], bool, Optional[str]]:
    raw = (origins_raw or "").strip()

    if not raw:
        if cookie_mode:
            return [], True, "CORS_ORIGINS is empty but cookie auth requires explicit origins."
        return "*", False, None

    if raw == "*":
        if cookie_mode:
            return [], True, "CORS_ORIGINS='*' is not allowed with cookie auth."
        return "*", False, None

    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        if cookie_mode:
            return [], True, "CORS_ORIGINS parsed empty but cookie auth requires explicit origins."
        return "*", False, None

    if cookie_mode:
        return origins, True, None

    return origins, False, None


def _import_attr(dotted: str, attr: str):
    try:
        mod = __import__(dotted, fromlist=[attr])
        return getattr(mod, attr), None
    except Exception as e:
        return None, f"{dotted}:{attr} -> {repr(e)}"


def _safe_get_env_bool(name: str) -> bool:
    return _truthy(os.getenv(name, ""))


def create_app() -> Flask:
    app = Flask(__name__)

    # ============================================================
    # SECRET KEY
    # ============================================================
    secret_key = os.environ.get("SECRET_KEY", "").strip()
    if not secret_key:
        if os.getenv("FLASK_ENV") == "development":
            secret_key = "dev-secret-key-do-not-use-in-production"
            warnings.warn("Using temporary SECRET_KEY (development only)")
        else:
            raise RuntimeError("SECRET_KEY is required in production")
    app.config["SECRET_KEY"] = secret_key

    # ============================================================
    # SESSION CONFIG
    # ============================================================
    app.config.update(
        SESSION_COOKIE_NAME="ntg_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=_safe_get_env_bool("SESSION_COOKIE_SECURE") or not os.getenv("FLASK_ENV") == "development",
        SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
        SESSION_COOKIE_PATH="/",
        PERMANENT_SESSION_LIFETIME=int(os.getenv("PERMANENT_SESSION_LIFETIME", "2592000")),
    )

    api_prefix = _normalize_api_prefix(API_PREFIX)

    # ============================================================
    # CORS
    # ============================================================
    cookie_mode = _cookie_mode_enabled()
    origins, supports_credentials, cors_err = _parse_origins(CORS_ORIGINS, cookie_mode=cookie_mode)
    if cors_err:
        raise RuntimeError(f"[CORS] {cors_err}")

    CORS(
        app,
        resources={rf"{api_prefix}/*": {"origins": origins}},
        supports_credentials=supports_credentials,
        allow_headers=[
            "Content-Type", "Authorization", "X-Auth-Token",
            "X-Requested-With", "X-Admin-Key", "X-Debug", "X-Request-Id",
        ],
        expose_headers=["Set-Cookie", "X-Request-Id"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
        vary_header=True,
    )

    # ============================================================
    # REQUEST ID
    # ============================================================
    @app.before_request
    def _assign_request_id():
        rid = (request.headers.get("X-Request-Id") or "").strip()
        if not rid:
            rid = str(uuid.uuid4())
        request.environ["REQUEST_ID"] = rid

    @app.after_request
    def _attach_request_id(resp):
        rid = str(request.environ.get("REQUEST_ID") or "")
        if rid:
            resp.headers["X-Request-Id"] = rid
        return resp

    def _rid() -> str:
        return str(request.environ.get("REQUEST_ID") or "")

    def _debug_enabled() -> bool:
        return (request.headers.get("X-Debug") or "").strip() == "1"

    # ============================================================
    # BLUEPRINT REGISTRATION
    # ============================================================
    boot: Dict[str, Any] = {
        "api_prefix": api_prefix,
        "registered": [],
        "failed": [],
    }

    def _register_bp(dotted: str, attr: str = "bp", *, required: bool = True):
        obj, err = _import_attr(dotted, attr)

        if obj is None:
            boot["failed"].append({"module": dotted, "error": err})
            if required:
                raise RuntimeError(f"Blueprint failed: {err}")
            return

        app.register_blueprint(obj, url_prefix=api_prefix)
        boot["registered"].append(dotted)

    # ============================================================
    # REQUIRED BLUEPRINTS (All files that exist)
    # ============================================================
    required_modules = [
        "app.routes.health",
        "app.routes.accounts",
        "app.routes.subscriptions",
        "app.routes.ask",
        "app.routes.web",
        "app.routes.webhooks",
        "app.routes.plans",
        "app.routes.billing",
        "app.routes.link_tokens",
        "app.routes.admin_link_tokens",
        "app.routes.accounts_admin",
        "app.routes.meta",
        "app.routes.email_link",
        "app.routes.web_auth",
        "app.routes.web_session",
        "app.routes.tax",
        "app.routes.workspace",
        "app.routes.link",
        "app.routes.referrals",
        "app.routes.entry",
        "app.routes.history",
    ]

    for m in required_modules:
        _register_bp(m, required=True)

    # ============================================================
    # OPTIONAL BLUEPRINTS (May not exist, won't crash if missing)
    # ============================================================
    optional_modules = [
        "app.routes.cron",
        "app.routes.whatsapp",
        "app.routes.telegram",
        "app.routes.web_ask",
        "app.routes.web_chat",
        "app.routes.paystack_webhook",
    ]

    for m in optional_modules:
        _register_bp(m, required=False)

    # ============================================================
    # ROUTE INSPECTOR
    # ============================================================
    @app.get(f"{api_prefix}/_routes")
    def list_routes():
        routes = []
        for rule in app.url_map.iter_rules():
            methods = sorted([m for m in rule.methods if m not in ("HEAD", "OPTIONS")])
            routes.append({"rule": str(rule), "methods": methods})

        return jsonify({
            "ok": True,
            "request_id": _rid(),
            "routes": sorted(routes, key=lambda x: x["rule"])
        }), 200

    # ============================================================
    # ERROR HANDLER
    # ============================================================
    @app.errorhandler(Exception)
    def _handle_error(e: Exception):
        return jsonify({
            "ok": False,
            "request_id": _rid(),
            "error": type(e).__name__,
            "message": str(e),
        }), getattr(e, "code", 500)

    return app
