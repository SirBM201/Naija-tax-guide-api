from __future__ import annotations

import os
from flask import Blueprint, jsonify

bp = Blueprint("debug_mail", __name__)

@bp.get("/debug/mail")
def debug_mail():
    # DO NOT expose secrets. Only show whether set.
    keys = [
        "MAIL_ENABLED","MAIL_HOST","MAIL_PORT","MAIL_USER","MAIL_PASS",
        "MAIL_FROM_EMAIL","MAIL_FROM_NAME","MAIL_USE_TLS",
        "SMTP_ENABLED","SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_PASS",
    ]
    out = {}
    for k in keys:
        v = os.getenv(k)
        if v is None:
            out[k] = None
        elif "PASS" in k:
            out[k] = "set" if str(v).strip() else ""
        else:
            out[k] = str(v).strip()
    return jsonify({"ok": True, "env": out})
