# app/main.py
import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

from app import create_app
from app.core.config import API_PREFIX


def _normalize_api_prefix(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "/api"
    if not v.startswith("/"):
        v = "/" + v
    return v.rstrip("/")


app = create_app()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@app.errorhandler(Exception)
def handle_any(err):
    if isinstance(err, HTTPException):
        return jsonify({"ok": False, "error": err.name}), err.code
    logging.exception("Unhandled error: %s", err)
    return jsonify({"ok": False, "error": "Internal Server Error"}), 500


api_prefix = _normalize_api_prefix(API_PREFIX)


@app.get(f"{api_prefix}/_routes")
def list_routes():
    out = []
    for r in sorted(app.url_map.iter_rules(), key=lambda x: str(x)):
        out.append({"rule": str(r), "methods": sorted(list(r.methods or []))})
    return jsonify({"ok": True, "routes": out})
