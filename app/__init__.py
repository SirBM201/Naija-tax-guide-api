# app/__init__.py
from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from app.core.config import API_PREFIX, CORS_ORIGINS

from app.routes.health import bp as health_bp
from app.routes.accounts import bp as accounts_bp
from app.routes.subscriptions import bp as subs_bp
from app.routes.ask import bp as ask_bp
from app.routes.webhooks import bp as webhooks_bp
from app.routes.plans import bp as plans_bp
from app.routes.link_tokens import bp as link_tokens_bp
from app.routes.whatsapp import bp as whatsapp_bp
from app.routes.admin_link_tokens import bp as admin_link_tokens_bp
from app.routes.debug_routes import bp as debug_routes_bp
from app.routes.accounts_admin import bp as accounts_admin_bp
from app.routes.meta import bp as meta_bp
from app.routes.email_link import bp as email_link_bp
from app.routes.cron import bp as cron_bp
from app.routes.web_auth import bp as web_auth_bp
from app.routes.web_session import bp as web_session_bp

# ✅ web ask endpoint
from app.routes.web_ask import bp as web_ask_bp

# ✅ billing endpoint
from app.routes.billing import bp as billing_bp

# ✅ web chat endpoints (NEW)
from app.routes.web_chat import bp as web_chat_bp

from app.routes.paystack import paystack_bp
from app.routes.paystack_webhook import bp as paystack_webhook_bp

try:
    from app.routes.telegram import bp as telegram_bp
except Exception:
    telegram_bp = None


def _normalize_api_prefix(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "/api"
    if not v.startswith("/"):
        v = "/" + v
    return v.rstrip("/")


def _parse_origins(origins_raw: str):
    raw = (origins_raw or "").strip()
    if not raw:
        return "*", False
    if raw == "*":
        return "*", False
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins, True


def create_app() -> Flask:
    app = Flask(__name__)

    api_prefix = _normalize_api_prefix(API_PREFIX)
    origins, supports_credentials = _parse_origins(CORS_ORIGINS)

    CORS(
        app,
        resources={rf"{api_prefix}/*": {"origins": origins}},
        supports_credentials=supports_credentials,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
    )

    # Core
    app.register_blueprint(health_bp, url_prefix=api_prefix)
    app.register_blueprint(accounts_bp, url_prefix=api_prefix)
    app.register_blueprint(subs_bp, url_prefix=api_prefix)

    # Existing unified ask (kept)
    app.register_blueprint(ask_bp, url_prefix=api_prefix)

    # Web auth + session
    app.register_blueprint(web_auth_bp, url_prefix=api_prefix)
    app.register_blueprint(web_session_bp, url_prefix=api_prefix)

    # ✅ Token-protected web ask
    app.register_blueprint(web_ask_bp, url_prefix=api_prefix)

    # ✅ Web chat API (sessions + messages)
    app.register_blueprint(web_chat_bp, url_prefix=api_prefix)

    # ✅ Billing route needed by frontend: /api/billing/me
    app.register_blueprint(billing_bp, url_prefix=api_prefix)

    app.register_blueprint(webhooks_bp, url_prefix=api_prefix)
    app.register_blueprint(plans_bp, url_prefix=api_prefix)

    app.register_blueprint(link_tokens_bp, url_prefix=api_prefix)
    app.register_blueprint(whatsapp_bp, url_prefix=api_prefix)
    if telegram_bp:
        app.register_blueprint(telegram_bp, url_prefix=api_prefix)

    app.register_blueprint(admin_link_tokens_bp, url_prefix=api_prefix)
    app.register_blueprint(debug_routes_bp, url_prefix=api_prefix)
    app.register_blueprint(accounts_admin_bp, url_prefix=api_prefix)
    app.register_blueprint(meta_bp, url_prefix=api_prefix)
    app.register_blueprint(email_link_bp, url_prefix=api_prefix)

    # Cron
    app.register_blueprint(cron_bp)

    # Paystack
    app.register_blueprint(paystack_bp, url_prefix=api_prefix)
    app.register_blueprint(paystack_webhook_bp, url_prefix=api_prefix)

    return app
