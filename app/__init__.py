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
        
        # Session cookie settings for cross-origin requests
        SESSION_COOKIE_SAMESITE='None',
        SESSION_COOKIE_SECURE=True,  # Set to False only for local HTTP development
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_PATH='/',
        
        # CORS settings
        CORS_ORIGINS=os.environ.get("CORS_ORIGINS", "https://www.naijataxguides.com,http://localhost:3000").split(","),
    )

    # Allow config override (e.g., for testing)
    if config_override:
        app.config.update(config_override)

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
    # Before request handler - ensure session is accessible
    # ------------------------------------------------------------
    @app.before_request
    def before_request():
        """Log request info and ensure session is loaded"""
        # Skip for static files and health checks
        if request.path.startswith('/static') or request.path == '/api/health':
            return
        
        logger.debug(f"Request: {request.method} {request.path}")
        logger.debug(f"Cookies present: {list(request.cookies.keys()) if request.cookies else 'None'}")
        
        # Session will be automatically loaded by Flask
        # This just ensures we can access it
        if session.get('user_id'):
            logger.debug(f"Session has user_id: {session.get('user_id')}")
        elif request.cookies.get('ntg_session'):
            logger.debug(f"ntg_session cookie found but not in Flask session yet")

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
