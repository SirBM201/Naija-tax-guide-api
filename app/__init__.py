import importlib
import logging
import os
from flask import Flask
from flask_cors import CORS
from flask_session import Session
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# List of all blueprints/modules to load automatically
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
    "app.routes.tax",          # <-- Tax filing endpoint
]

def create_app(config_override=None):
    app = Flask(__name__)

    # ------------------------------------------------------------
    # Base configuration
    # ------------------------------------------------------------
    app.config.update(
        # Session settings (required for cross-origin cookie support)
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
        SESSION_TYPE="filesystem",          # or "redis" if you prefer
        SESSION_COOKIE_SAMESITE="None",     # Allow cross-site requests
        SESSION_COOKIE_SECURE=True,         # Requires HTTPS (set False for local dev)
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_PATH="/",
        PERMANENT_SESSION_LIFETIME=86400,   # 1 day

        # CORS (overridden by flask-cors below, but kept for reference)
        CORS_ORIGINS=os.environ.get("CORS_ORIGINS", "https://www.naijataxguides.com").split(","),
    )

    # Allow config override (e.g., for testing)
    if config_override:
        app.config.update(config_override)

    # ------------------------------------------------------------
    # CORS setup – allow credentials from frontend domain
    # ------------------------------------------------------------
    CORS(app,
         origins=app.config["CORS_ORIGINS"],
         supports_credentials=True,
         allow_headers=["Content-Type", "Authorization"],
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

    # ------------------------------------------------------------
    # Session init
    # ------------------------------------------------------------
    Session(app)

    # ------------------------------------------------------------
    # Automatic blueprint registration
    # ------------------------------------------------------------
    for module_name in required_modules:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "bp"):
                app.register_blueprint(module.bp, url_prefix="/api")
                logger.info(f"Registered blueprint: {module_name}")
            else:
                logger.warning(f"Module {module_name} has no 'bp' attribute")
        except ImportError as e:
            logger.error(f"Failed to import {module_name}: {e}")

    # ------------------------------------------------------------
    # Health check endpoint (simple)
    # ------------------------------------------------------------
    @app.route("/api/health", methods=["GET"])
    def health():
        return {"ok": True, "status": "healthy"}

    return app
