from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.services.auth_service import get_current_user
from app.services.credit_usage_service import deduct_credits
from app.services.web_quiz_service import (
    _find_question_by_id_or_code,
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


def _quiz_question_from_body(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    question_id = _safe_text(body.get('question_id') or body.get('db_id'))
    question_code = _safe_text(body.get('question_code') or body.get('id'))
    return _find_question_by_id_or_code(question_id, question_code)


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


@bp.post('/web/quiz/explanation')
def web_quiz_detailed_explanation():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status

    body: Dict[str, Any] = request.get_json(silent=True) or {}
    question = _quiz_question_from_body(body)
    if not question:
        return jsonify({
            'ok': False,
            'error': 'question_not_found',
            'message': 'Quiz question could not be found. Please answer a new question and try again.',
        }), 404

    explanation = _safe_text(question.get('premium_explanation') or question.get('short_explanation'))
    if not explanation:
        return jsonify({
            'ok': False,
            'error': 'explanation_not_available',
            'message': 'A saved detailed explanation is not available for this question yet.',
        }), 400

    reference = f"WEB-Q5-{uuid.uuid4().hex[:12].upper()}"
    question_code = _safe_text(question.get('question_code') or question.get('id'))
    debit = deduct_credits(
        account_id=account_id,
        action_code='ai_quiz_explanation',
        source_kind='ai',
        channel='web',
        description='Q5 detailed saved quiz explanation',
        reference=reference,
        metadata={
            'source': 'web_quiz_q5_saved_explanation',
            'live_ai_called': False,
            'question_code': question_code,
            'question_id': _safe_text(question.get('db_id')),
            'category': _safe_text(question.get('category') or 'General'),
        },
    )

    if not debit.get('ok'):
        error = _safe_text(debit.get('error') or debit.get('reason') or 'credit_required')
        if error == 'paid_plan_required':
            message = 'Q5 detailed explanation requires an active paid plan.'
        elif error == 'insufficient_credits':
            message = 'Q5 costs 1 Usage Credit, but your balance is not enough.'
        else:
            message = 'Q5 detailed explanation could not be unlocked right now.'
        return jsonify({
            'ok': False,
            'error': error,
            'message': message,
            'credit': debit,
        }), 402

    return jsonify({
        'ok': True,
        'explanation': explanation,
        'source_reference': question.get('source_reference') or 'Naija Tax Guide quiz bank',
        'credit': {
            'deducted': bool(debit.get('deducted')),
            'credits_deducted': debit.get('credits_deducted') or debit.get('credit_cost') or 1,
            'balance_before': debit.get('balance_before'),
            'balance_after': debit.get('balance_after'),
            'reference': reference,
            'live_ai_called': False,
        },
    }), 200


@bp.get('/web/quiz/score')
def web_quiz_score():
    account_id, error_response, status = _require_quiz_account()
    if not account_id:
        return error_response, status

    res = get_quiz_score(account_id=account_id)
    return jsonify(res), (200 if res.get('ok') else 400)
