from __future__ import annotations

from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.services.auth_service import get_current_user
from app.services.web_quiz_service import (
    get_quiz_categories,
    get_quiz_question,
    get_quiz_score,
    submit_quiz_answer,
)

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover
    get_account_id_from_request = None  # type: ignore

bp = Blueprint('web_quiz', __name__)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _current_account_id() -> Optional[str]:
    """
    Resolve the logged-in web account using the same session path that already
    works for /api/me, /api/workspace/limits, and /api/link/status.

    Fallback to token/cookie auth is kept for compatibility, but a stale token
    cookie should no longer block a valid Flask web session.
    """
    try:
        user = get_current_user()
    except Exception:
        user = None

    if user:
        account_id = _safe_text(user.get('account_id') or user.get('id'))
        if account_id:
            return account_id

    if get_account_id_from_request is not None:
        try:
            account_id, _debug = get_account_id_from_request(request)  # type: ignore[misc]
            account_id = _safe_text(account_id)
            if account_id:
                return account_id
        except Exception:
            pass

    return None


def _require_quiz_account() -> tuple[Optional[str], Any, int]:
    account_id = _current_account_id()
    if not account_id:
        return None, jsonify({
            'ok': False,
            'error': 'login_required',
            'message': 'Please sign in again to use the quiz.',
        }), 401
    return account_id, None, 200


@bp.get('/web/quiz/categories')
def web_quiz_categories():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status
    return jsonify(get_quiz_categories()), 200


@bp.get('/web/quiz/question')
def web_quiz_question():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status

    category = _safe_text(request.args.get('category'))
    exclude_codes = _safe_text(request.args.get('exclude') or request.args.get('seen') or '')
    res = get_quiz_question(account_id=account_id, category=category, exclude_codes=exclude_codes)
    return jsonify(res), (200 if res.get('ok') else 400)


@bp.post('/web/quiz/answer')
def web_quiz_answer():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status

    body: Dict[str, Any] = request.get_json(silent=True) or {}
    res = submit_quiz_answer(account_id=account_id, body=body)
    return jsonify(res), (200 if res.get('ok') else 400)


@bp.get('/web/quiz/score')
def web_quiz_score():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status

    res = get_quiz_score(account_id=account_id)
    return jsonify(res), (200 if res.get('ok') else 400)
