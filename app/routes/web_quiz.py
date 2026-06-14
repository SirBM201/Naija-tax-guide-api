from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, g, jsonify, request

from app.core.auth import require_auth_plus
from app.services.web_quiz_service import get_quiz_categories, get_quiz_question, get_quiz_score, submit_quiz_answer

bp = Blueprint('web_quiz', __name__)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


@bp.get('/web/quiz/categories')
@require_auth_plus
def web_quiz_categories():
    return jsonify(get_quiz_categories()), 200


@bp.get('/web/quiz/question')
@require_auth_plus
def web_quiz_question():
    account_id = _safe_text(getattr(g, 'account_id', None))
    category = _safe_text(request.args.get('category'))
    res = get_quiz_question(account_id=account_id, category=category)
    return jsonify(res), (200 if res.get('ok') else 400)


@bp.post('/web/quiz/answer')
@require_auth_plus
def web_quiz_answer():
    account_id = _safe_text(getattr(g, 'account_id', None))
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    res = submit_quiz_answer(account_id=account_id, body=body)
    return jsonify(res), (200 if res.get('ok') else 400)


@bp.get('/web/quiz/score')
@require_auth_plus
def web_quiz_score():
    account_id = _safe_text(getattr(g, 'account_id', None))
    res = get_quiz_score(account_id=account_id)
    return jsonify(res), (200 if res.get('ok') else 400)
