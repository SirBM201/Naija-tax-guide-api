import importlib
import logging
import os
from flask import Flask, session, request, g
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
    "app.routes.tax",
]

def create_app(config_override=None):
    app = Flask(__name__)

    # ------------------------------------------------------------
    # Base configuration
    # ------------------------------------------------------------
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
        
        # Session configuration - use filesystem for Koyeb
        SESSION_TYPE='filesystem',
        SESSION_FILE_DIR='/tmp/flask_sessions',
        SESSION_FILE_THRESHOLD=500,
        SESSION_FILE_MODE=0o600,
        
        # Session cookie settings - critical for cross-domain proxy
        SESSION_COOKIE_NAME="ntg_session",
        SESSION_COOKIE_SAMESITE='Lax',  # Lax works with proxy (same domain)
        SESSION_COOKIE_SECURE=True,      # Must be True for HTTPS
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_DOMAIN=None,      # None = current domain only
        
        # Permanent session lifetime (30 days)
        PERMANENT_SESSION_LIFETIME=2592000,
        
        # CORS settings
        CORS_ORIGINS=os.environ.get("CORS_ORIGINS", "https://www.naijataxguides.com,http://localhost:3000").split(","),
    )

    # Allow config override (e.g., for testing)
    if config_override:
        app.config.update(config_override)

    # Initialize session extension
    Session(app)

    # ------------------------------------------------------------
    # CORS setup – allow credentials from frontend domains
    # ------------------------------------------------------------
    CORS(app,
         origins=app.config["CORS_ORIGINS"],
         supports_credentials=True,
         allow_headers=["Content-Type", "Authorization", "Cookie"],
         expose_headers=["Set-Cookie"],
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

    # ------------------------------------------------------------
    # Before request handler
    # ------------------------------------------------------------
    @app.before_request
    def before_request():
        """Load session before each request"""
        if request.path.startswith('/static') or request.path == '/api/health':
            return
        
        # Force session to load
        session.modified = True
        
        logger.info(f"Request: {request.method} {request.path}")
        logger.info(f"Session keys after load: {list(session.keys()) if session else 'None'}")
        logger.info(f"Session user_id: {session.get('user_id')}")
        
        # Set user in g if exists in session
        if session.get('user_id'):
            g.user = {
                "id": session.get('user_id'),
                "email": session.get('user_email'),
                "account_id": session.get('account_id') or session.get('user_id'),
            }

    # ------------------------------------------------------------
    # After request handler
    # ------------------------------------------------------------
    @app.after_request
    def after_request(response):
        """Save session after request"""
        if not request.path.startswith('/static') and request.path != '/api/health':
            logger.info(f"After request - Session keys: {list(session.keys()) if session else 'None'}")
            logger.info(f"After request - Session user_id: {session.get('user_id')}")
        return response

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
    # Simple health check endpoint
    # ------------------------------------------------------------
    @app.route('/api/health', methods=['GET'])
    def health():
        return {"ok": True, "status": "healthy"}

    return app
