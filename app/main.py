# app/main.py
import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

from app import create_app

app = create_app()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@app.errorhandler(Exception)
def handle_any(err):
    if isinstance(err, HTTPException):
        return jsonify({"ok": False, "error": err.name}), err.code
    logging.exception("Unhandled error: %s", err)
    return jsonify({"ok": False, "error": "Internal Server Error"}), 500
