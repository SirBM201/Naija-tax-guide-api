# app/__init__.py
from __future__ import annotations

from flask import Flask, jsonify
from flask_cors import CORS

from .core.config import API_PREFIX, CORS_ORIGINS


def _apply_cors(app: Flask) -> None:
    origins = (CORS_ORIGINS or "*").strip()
    if origins == "*":
        CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
        return

    origin_list = [o.strip() for o in origins.split(",") if o.strip()]
    CORS(app, resources={r"/*": {"origins": origin_list}}, supports_credentials=True)


def _register_blueprints_once(app: Flask, blueprints: list) -> None:
    """
    Registers each blueprint only once.
    - guards against duplicates in your blueprints list
    - guards against accidental double imports
    """
    seen = set()

    for bp in blueprints:
        if bp is None:
            continue

        key = (getattr(bp, "name", None), id(bp))
        if key in seen:
            # skip duplicates safely
            continue
        seen.add(key)

        # Prefix support (your API_PREFIX system)
        if API_PREFIX:
            app.register_blueprint(bp, url_prefix=API_PREFIX)
        else:
            app.register_blueprint(bp)


def create_app() -> Flask:
    app = Flask(__name__)

    # -----------------------------
    # Core config
    # -----------------------------
    app.config["JSON_SORT_KEYS"] = False
    _apply_cors(app)

    # -----------------------------
    # Health
    # -----------------------------
    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # -----------------------------
    # Import blueprints (EDIT THESE IMPORTS to match your repo)
    # -----------------------------
    # IMPORTANT: only import each blueprint ONCE here.

    from .routes.paystack_webhook import bp as paystack_webhook_bp  # ✅ updated name
    # from .routes.auth import bp as auth_bp
    # from .routes.web_session import bp as web_session_bp
    # from .routes.ask import bp as ask_bp
    # from .routes.plans import bp as plans_bp
    # from .routes.subscriptions import bp as subscriptions_bp
    # ...add your others...

    blueprints = [
        paystack_webhook_bp,
        # auth_bp,
        # web_session_bp,
        # ask_bp,
        # plans_bp,
        # subscriptions_bp,
        # ...
    ]

    _register_blueprints_once(app, blueprints)

    return app
