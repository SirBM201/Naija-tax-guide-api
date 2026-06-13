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
            return ["https://www.naijataxguides.com", "https://naijataxguides.com"], True, None
        return "*", False, None

    if raw == "*":
        if cookie_mode:
            return [], True, "CORS_ORIGINS='*' is not allowed with cookie auth."
        return "*", False, None

    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        if cookie_mode:
            return ["https://www.naijataxguides.com", "https://naijataxguides.com"], True, None
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
    # SECRET KEY CONFIGURATION
    # ============================================================
    secret_key = os.environ.get("SECRET_KEY", "").strip()
    if not secret_key:
        if os.getenv("FLASK_ENV") == "development":
            secret_key = "dev-secret-key-do-not-use-in-production"
            warnings.warn("Using temporary SECRET_KEY (development only)")
        else:
            raise RuntimeError(
                "SECRET_KEY environment variable is required in production. "
                "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
    app.config["SECRET_KEY"] = secret_key

    # ============================================================
    # SESSION CONFIGURATION
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
    # CORS CONFIGURATION
    # ============================================================
    cookie_mode = _cookie_mode_enabled()
    origins, supports_credentials, cors_err = _parse_origins(CORS_ORIGINS, cookie_mode=cookie_mode)
    if cors_err:
        raise RuntimeError(f"[CORS] {cors_err}")

    if cookie_mode and origins != "*":
        frontend_domains = ["https://www.naijataxguides.com", "https://naijataxguides.com"]
        if isinstance(origins, list):
            for fd in frontend_domains:
                if fd not in origins:
                    origins.append(fd)
        supports_credentials = True

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
        vary_header=True,
    )

    # ============================================================
    # REQUEST ID MIDDLEWARE
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

        if request.path.startswith(f"{api_prefix}/web/auth/"):
            resp.headers["Cache-Control"] = "no-store"

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
        "cookie_mode": cookie_mode,
        "cors": {"origins": origins, "supports_credentials": supports_credentials},
        "strict": (os.getenv("STRICT_BLUEPRINTS", "1").strip() != "0"),
        "debug_routes_enabled": _safe_get_env_bool("ENABLE_DEBUG_ROUTES"),
        "registered": [],
        "failed": [],
    }
    strict = bool(boot["strict"])

    def _register_bp(
        dotted: str,
        attr: str = "bp",
        *,
        alias_name: Optional[str] = None,
        required: bool = True,
        url_prefix: Optional[str] = api_prefix,
    ):
        obj, err = _import_attr(dotted, attr)
        entry: Dict[str, Any] = {
            "module": dotted,
            "attr": attr,
            "alias_name": alias_name or dotted.split(".")[-1],
            "url_prefix": url_prefix,
            "required": required,
        }

        if obj is None:
            entry["error"] = err
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(f"[boot] REQUIRED blueprint import failed: {err}")
            return

        bp_name = getattr(obj, "name", None) or f"{dotted}:{attr}"

        if not hasattr(app, "_bp_names"):
            app._bp_names = set()
        if bp_name in app._bp_names:
            msg = f"[boot] Duplicate blueprint name detected: {bp_name} from {dotted}:{attr}"
            entry["error"] = msg
            boot["failed"].append(entry)
            if required and strict:
                raise RuntimeError(msg)
            return
        app._bp_names.add(bp_name)

        if url_prefix is not None:
            app.register_blueprint(obj, url_prefix=url_prefix)
        else:
            app.register_blueprint(obj)

        entry["bp_name"] = bp_name
        boot["registered"].append(entry)

    # ============================================================
    # REQUIRED BLUEPRINTS (CORE API - ALL EXISTING FILES)
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
        "app.routes.link",
        "app.routes.link_tokens",
        "app.routes.admin_link_tokens",
        "app.routes.accounts_admin",
        "app.routes.meta",
        "app.routes.email_link",
        "app.routes.web_auth",
        "app.routes.web_session",
        "app.routes.tax",
        "app.routes.workspace",
        "app.routes.referrals",
        "app.routes.entry",
        "app.routes.history",
        "app.routes.support",
        "app.routes.deadlines",
        "app.routes.channel",
        "app.routes.channel_payment_return",
        "app.routes.whatsapp",
    ]

    for dotted in required_modules:
        _register_bp(dotted, "bp", required=True, url_prefix=api_prefix)

    # ============================================================
    # RUNTIME PATCHES
    # ============================================================
    try:
        from app.services.whatsapp_display_patch import apply_whatsapp_display_patch

        apply_whatsapp_display_patch()
        boot["registered"].append(
            {
                "module": "app.services.whatsapp_display_patch",
                "attr": "apply_whatsapp_display_patch",
                "alias_name": "whatsapp_display_patch",
                "url_prefix": None,
                "required": False,
            }
        )
    except Exception as e:
        boot["failed"].append(
            {
                "module": "app.services.whatsapp_display_patch",
                "attr": "apply_whatsapp_display_patch",
                "alias_name": "whatsapp_display_patch",
                "url_prefix": None,
                "required": False,
                "error": repr(e),
            }
        )

    try:
        from app.services.ask_relevance_patch import apply_ask_relevance_patch

        apply_ask_relevance_patch()
        boot["registered"].append(
            {
                "module": "app.services.ask_relevance_patch",
                "attr": "apply_ask_relevance_patch",
                "alias_name": "ask_relevance_patch",
                "url_prefix": None,
                "required": False,
            }
        )
    except Exception as e:
        boot["failed"].append(
            {
                "module": "app.services.ask_relevance_patch",
                "attr": "apply_ask_relevance_patch",
                "alias_name": "ask_relevance_patch",
                "url_prefix": None,
                "required": False,
                "error": repr(e),
            }
        )

    # ============================================================
    # OPTIONAL BLUEPRINTS (WON'T CRASH IF MISSING)
    # ============================================================
    optional_modules = [
        "app.routes.cron",
        "app.routes.telegram",
        "app.routes.web_ask",
        "app.routes.web_chat",
        "app.routes.paystack_webhook",
        "app.routes.referral_hub",
        "app.routes.promo",
        "app.routes.channel_promo",
    ]

    for dotted in optional_modules:
        _register_bp(dotted, "bp", required=False, url_prefix=api_prefix)

    # ============================================================
    # DEBUG BLUEPRINTS (ONLY WHEN ENABLED)
    # ============================================================
    if _safe_get_env_bool("ENABLE_DEBUG_ROUTES"):
        _register_bp("app.routes._debug", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_routes", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_auth", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_mail", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.debug_otp", "bp", required=False, url_prefix=api_prefix)
        _register_bp("app.routes.paystack_debug", "bp", required=False, url_prefix=api_prefix)

    # ============================================================
    # ROUTE INSPECTOR
    # ============================================================
    @app.get(f"{api_prefix}/_debug_routes")
    def debug_list_routes():
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
    # BOOT REPORT ENDPOINT
    # ============================================================
    @app.get(f"{api_prefix}/_boot")
    def boot_report():
        admin_key_set = bool((os.getenv("ADMIN_KEY") or "").strip())
        return jsonify(
            {
                "ok": True,
                "request_id": _rid(),
                "admin_key_set": admin_key_set,
                "boot": boot,
            }
        ), 200

    # ============================================================
    # RUNTIME DIAGNOSTICS ENDPOINT
    # ============================================================
    @app.get(f"{api_prefix}/_diag")
    def runtime_diag():
        hints: List[str] = []

        cron_registered = any((r.get("alias_name") == "cron") for r in boot.get("registered", []))
        if not cron_registered:
            hints.append("Cron blueprint is NOT registered.")

        if cookie_mode and origins == "*":
            hints.append("COOKIE_MODE is enabled but CORS origins are '*'. Use explicit origins when cookies are used.")

        if cookie_mode and (isinstance(origins, list) and not origins):
            hints.append("COOKIE_MODE is enabled but parsed origins list is empty. Set CORS_ORIGINS to your frontend URL(s).")

        if not (os.getenv("SUPABASE_URL") or "").strip():
            hints.append("SUPABASE_URL is missing -> Supabase calls will fail.")
        if not (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip():
            hints.append("Supabase service key is missing -> RPC/table calls may fail.")

        env_view = {
            "SECRET_KEY_SET": bool(app.config.get("SECRET_KEY") and app.config["SECRET_KEY"] != "dev-secret-key-do-not-use-in-production"),
            "ADMIN_KEY_SET": bool((os.getenv("ADMIN_KEY") or "").strip()),
            "API_PREFIX": api_prefix,
            "COOKIE_MODE": cookie_mode,
            "CORS_ORIGINS_MODE": ("*" if origins == "*" else "list"),
            "ENABLE_DEBUG_ROUTES": _safe_get_env_bool("ENABLE_DEBUG_ROUTES"),
            "STRICT_BLUEPRINTS": strict,
            "SUPPORTS_CREDENTIALS": supports_credentials,
            "WEB_AUTH_ENABLED": _safe_get_env_bool("WEB_AUTH_ENABLED"),
        }

        return jsonify({"ok": True, "request_id": _rid(), "env": env_view, "hints": hints}), 200

    # ============================================================
    # PREFLIGHT SAFETY NET
    # ============================================================
    @app.route(f"{api_prefix}/<path:_any>", methods=["OPTIONS"])
    def _api_preflight(_any: str):
        return ("", 204)

    # ============================================================
    # GLOBAL ERROR HANDLER
    # ============================================================
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

        if _debug_enabled():
            import traceback as _tb

            out["debug"] = {
                "path": request.path,
                "method": request.method,
                "content_type": request.content_type,
            }
            out["traceback"] = _tb.format_exc(limit=60)

        return jsonify(out), status

    return app
