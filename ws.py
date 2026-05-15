import os
import logging
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__)

# Secret key for sessions
app.secret_key = os.getenv('SECRET_KEY', 'default-secret-key-change-in-production')

# Session configuration
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_DOMAIN'] = '.koyeb.app'  # Allow subdomains

# CORS configuration - Allow your frontend domain
CORS(app, 
     supports_credentials=True,
     origins=[
         'https://www.naijataxguides.com',
         'https://naijataxguides.com',
         'http://localhost:3000',
         'http://localhost:5173'
     ],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
     allow_headers=['Content-Type', 'Authorization', 'X-Requested-With'],
     expose_headers=['Content-Type', 'Set-Cookie'])

logging.basicConfig(level=logging.INFO)

# ============ IMPORT AND REGISTER ALL BLUEPRINTS ============
# ============ IMPORT AND REGISTER ALL BLUEPRINTS ============

# WhatsApp blueprint
from app.routes.whatsapp import bp as whatsapp_bp
app.register_blueprint(whatsapp_bp, url_prefix='/api')

# Workspace blueprints
from app.routes.workspace import bp as workspace_bp
from app.routes.workspace_members import bp as workspace_members_bp
app.register_blueprint(workspace_bp, url_prefix='/api/workspace')
app.register_blueprint(workspace_members_bp, url_prefix='/api/workspace')

# Web auth and web routes
from app.routes.web_auth import bp as web_auth_bp
from app.routes.web_ask import bp as web_ask_bp
from app.routes.web_session import bp as web_session_bp
from app.routes.web import bp as web_bp
app.register_blueprint(web_auth_bp, url_prefix='/api')
app.register_blueprint(web_ask_bp, url_prefix='/api')
app.register_blueprint(web_session_bp, url_prefix='/api')
app.register_blueprint(web_bp, url_prefix='/api')

# Billing and subscriptions
from app.routes.billing import bp as billing_bp
from app.routes.subscriptions import bp as subscriptions_bp
from app.routes.plans import bp as plans_bp
app.register_blueprint(billing_bp, url_prefix='/api/billing')
app.register_blueprint(subscriptions_bp, url_prefix='/api')
app.register_blueprint(plans_bp, url_prefix='/api')

# User account routes
from app.routes.me import bp as me_bp
from app.routes.accounts import bp as accounts_bp
app.register_blueprint(me_bp, url_prefix='/api')
app.register_blueprint(accounts_bp, url_prefix='/api')

# Referrals
from app.routes.referrals import bp as referrals_bp
app.register_blueprint(referrals_bp, url_prefix='/api')

# History
from app.routes.history import bp as history_bp
app.register_blueprint(history_bp, url_prefix='/api')

# Link and channel management
from app.routes.link import bp as link_bp
from app.routes.link_tokens import bp as link_tokens_bp
from app.routes.channel import bp as channel_bp
from app.routes.channel_access import bp as channel_access_bp
app.register_blueprint(link_bp, url_prefix='/api')
app.register_blueprint(link_tokens_bp, url_prefix='/api')
app.register_blueprint(channel_bp, url_prefix='/api')
app.register_blueprint(channel_access_bp, url_prefix='/api')

# Tax and filing
from app.routes.tax import bp as tax_bp
app.register_blueprint(tax_bp, url_prefix='/api')

# Health, entry, cron
from app.routes.health import bp as health_bp
from app.routes.entry import bp as entry_bp
from app.routes.cron import bp as cron_bp
app.register_blueprint(health_bp, url_prefix='/api')
app.register_blueprint(entry_bp, url_prefix='/api')
app.register_blueprint(cron_bp, url_prefix='/api')

# Telegram (future)
from app.routes.telegram import bp as telegram_bp
app.register_blueprint(telegram_bp, url_prefix='/api')

print("All blueprints registered successfully")
print("Available endpoints:")
print("  - WhatsApp: /api/whatsapp/webhook")
print("  - Web auth: /api/web/auth/request-otp")
print("  - Web workspace: /api/workspace/limits")
print("  - Billing: /api/billing/me")
print("  - Plans: /api/plans")
print("  - Health: /api/health")

# ============ RUN APPLICATION ============

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
