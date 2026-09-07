# app/main.py
import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

from app import create_app
from app.routes.channel_activation import bp as channel_activation_bp
from app.services.v1_security_guard import install_v1_security_guard
from app.services.v1_subscription_reconciliation import install_v1_subscription_reconciliation
from app.services.channel_runtime_http_guard import install_channel_runtime_http_guard
from app.services.billing_atomic_subscription_patch import install as install_billing_atomic_subscription_patch

app = create_app()
app.register_blueprint(channel_activation_bp, url_prefix="/api")
install_v1_security_guard(app)
install_channel_runtime_http_guard(app)
install_v1_subscription_reconciliation()
install_billing_atomic_subscription_patch()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@app.errorhandler(Exception)
def handle_any(err):
    if isinstance(err, HTTPException):
        return jsonify({"ok": False, "error": err.name}), err.code
    logging.exception("Unhandled error: %s", err)
    return jsonify({"ok": False, "error": "Internal Server Error"}), 500