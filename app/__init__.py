import importlib
import logging
import os
from flask import Flask, session, request, g
from flask_cors import CORS
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
    "app.routes.tax",
]

def create_app(config_override=None):
    app = Flask(__name__)

    # ------------------------------------------------------------
    # Base configuration
    # ------------------------------------------------------------
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
        
        # Session cookie settings
        SESSION_COOKIE_NAME="ntg_session",
        SESSION_COOKIE_SAMESITE='Lax',  # Changed from 'None' for same-domain
        SESSION_COOKIE_SECURE=True,      # Keep True for HTTPS
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_DOMAIN='.naijataxguides.com',  # IMPORTANT: dot prefix for subdomains
        
        # Permanent session lifetime (30 days)
        PERMANENT_SESSION_LIFETIME=2592000,
        
        # CORS settings - now only need same domain
        FRONTEND_URL=os.environ.get("FRONTEND_URL", "https://www.naijataxguides.com"),
    )

    # Allow config override (e.g., for testing)
    if config_override:
        app.config.update(config_override)

    # ------------------------------------------------------------
    # CORS setup – now only need to allow frontend domain
    # ------------------------------------------------------------
    CORS(app,
         origins=[app.config["FRONTEND_URL"]],
         supports_credentials=True,
         allow_headers=["Content-Type", "Authorization", "Cookie"],
         expose_headers=["Set-Cookie"],
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

    # ------------------------------------------------------------
    # Before request handler
    # ------------------------------------------------------------
    @app.before_request
    def before_request():
        """Log request info"""
        if request.path.startswith('/static') or request.path == '/api/health':
            return
        logger.debug(f"Request: {request.method} {request.path}")
        logger.debug(f"Session user_id: {session.get('user_id')}")

    # ------------------------------------------------------------
    # Automatic blueprint registration
    # ------------------------------------------------------------
    for module_name in required_modules:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, 'bp'):
                app.register_blueprint(module.bp, url_prefix='/api')
                logger.info(f"Registered blueprint: {module_name}")
            else:
                logger.warning(f"Module {module_name} has no 'bp' attribute")
        except ImportError as e:
            logger.error(f"Failed to import {module_name}: {e}")

    # ------------------------------------------------------------
    # Health check endpoint
    # ------------------------------------------------------------
    @app.route('/api/health', methods=['GET'])
    def health():
        return {"ok": True, "status": "healthy"}

    return app
