import os
import logging
from flask import Flask
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'MmFlODZiNGEtYWNkZC00OTk2LWFmMTgtNzc3Zjg1MjQzMGE1')
logging.basicConfig(level=logging.INFO)

# ============ IMPORT AND REGISTER ALL BLUEPRINTS ============

# WhatsApp blueprint
from app.routes.whatsapp import bp as whatsapp_bp
app.register_blueprint(whatsapp_bp, url_prefix='/api')

# Web blueprints
from app.routes.workspace import bp as workspace_bp
from app.routes.web_auth import bp as web_auth_bp
from app.routes.web_ask import bp as web_ask_bp
from app.routes.billing import bp as billing_bp
from app.routes.me import bp as me_bp
from app.routes.plans import bp as plans_bp
from app.routes.referrals import bp as referrals_bp
from app.routes.history import bp as history_bp
from app.routes.web_session import bp as web_session_bp
from app.routes.accounts import bp as accounts_bp
from app.routes.tax import bp as tax_bp
from app.routes.subscriptions import bp as subscriptions_bp
from app.routes.health import bp as health_bp
from app.routes.entry import bp as entry_bp
from app.routes.web import bp as web_bp
from app.routes.telegram import bp as telegram_bp
from app.routes.cron import bp as cron_bp

# Register all web blueprints
app.register_blueprint(workspace_bp, url_prefix='/api/workspace')
app.register_blueprint(web_auth_bp, url_prefix='/api')
app.register_blueprint(web_ask_bp, url_prefix='/api/web')
app.register_blueprint(billing_bp, url_prefix='/api/billing')
app.register_blueprint(me_bp, url_prefix='/api')
app.register_blueprint(plans_bp, url_prefix='/api')
app.register_blueprint(referrals_bp, url_prefix='/api')
app.register_blueprint(history_bp, url_prefix='/api')
app.register_blueprint(web_session_bp, url_prefix='/api')
app.register_blueprint(accounts_bp, url_prefix='/api')
app.register_blueprint(tax_bp, url_prefix='/api')
app.register_blueprint(subscriptions_bp, url_prefix='/api')
app.register_blueprint(health_bp, url_prefix='/api')
app.register_blueprint(entry_bp, url_prefix='/api')
app.register_blueprint(web_bp, url_prefix='/api')
app.register_blueprint(telegram_bp, url_prefix='/api')
app.register_blueprint(cron_bp, url_prefix='/api')

print("All blueprints registered successfully")
print("Available endpoints:")
print("  - WhatsApp: /api/whatsapp/webhook")
print("  - Web auth: /api/web/auth/request-otp")
print("  - Web workspace: /api/workspace/limits")
print("  - Plans: /api/plans")
print("  - Health: /api/health")

# ============ RUN APPLICATION ============

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
