from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.core.supabase_client import supabase

try:
    from app.services.channel_subscription_service import has_active_subscription
except Exception:  # pragma: no cover
    has_active_subscription = None  # type: ignore


WEB_QUIZ_SERVICE_VERSION = "2026-06-14-v1-web-quiz"
QUIZ_FREE_DAILY_LIMIT = 12

FALLBACK_QUIZ_BANK: List[Dict[str, Any]] = [
    {
        "id": "web_q_paye_1",
        "category": "PAYE",
        "difficulty": "basic",
        "question": "Which Nigerian tax is normally deducted from employee salaries by the employer?",
        "options": [
            {"option_id": "web_q_paye_1_A", "option_text": "Value Added Tax charged on sales invoices", "is_correct": False},
            {"option_id": "web_q_paye_1_B", "option_text": "PAYE deducted from employment income", "is_correct": True},
            {"option_id": "web_q_paye_1_C", "option_text": "Company Income Tax charged on company profit", "is_correct": False},
            {"option_id": "web_q_paye_1_D", "option_text": "Withholding Tax deducted from supplier invoices only", "is_correct": False},
        ],
        "short_explanation": "PAYE is deducted from employment income and remitted by the employer to the relevant State Internal Revenue Service.",
        "premium_explanation": "PAYE means Pay-As-You-Earn. It is a payroll tax mechanism where the employer deducts tax from salaries and remits it to the relevant State Internal Revenue Service.",
    },
    {
        "id": "web_q_vat_1",
        "category": "VAT",
        "difficulty": "basic",
        "question": "VAT in Nigeria is best described as which type of tax?",
        "options": [
            {"option_id": "web_q_vat_1_A", "option_text": "A consumption tax on taxable supplies of goods and services", "is_correct": True},
            {"option_id": "web_q_vat_1_B", "option_text": "A tax on only company net profit after expenses", "is_correct": False},
            {"option_id": "web_q_vat_1_C", "option_text": "A salary tax deducted only from employees", "is_correct": False},
            {"option_id": "web_q_vat_1_D", "option_text": "A penalty charged only when returns are filed late", "is_correct": False},
        ],
        "short_explanation": "VAT is a consumption tax charged on many taxable goods and services.",
        "premium_explanation": "VAT is charged on taxable supplies. Businesses may charge output VAT and claim allowable input VAT before remitting net VAT where applicable.",
    },
]


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value).lower()).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_bounds() -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _rows(resp: Any) -> List[Dict[str, Any]]:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _safe_exec(builder: Any) -> Tuple[bool, Any, Optional[str]]:
    try:
        resp = builder.execute()
        return True, resp, None
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {str(exc)[:600]}"


def _is_paid(account_id: str) -> bool:
    if not _clean(account_id):
        return False
    if has_active_subscription is not None:
        try:
            return bool(has_active_subscription(account_id))
        except Exception:
            pass
    for table in ("user_subscriptions", "subscriptions"):
        try:
            res = (
                _sb()
                .table(table)
                .select("id,status,is_active,active,expires_at,current_period_end")
                .eq("account_id", account_id)
                .limit(3)
                .execute()
            )
            for row in _rows(res):
                status = _norm(row.get("status"))
                if row.get("is_active") is True or row.get("active") is True or status == "active":
                    return True
        except Exception:
            continue
    return False


def _daily_attempt_count(account_id: str) -> int:
    if not _clean(account_id):
        return 0
    start, end = _today_bounds()
    try:
        res = (
            _sb()
            .table("tax_quiz_attempts")
            .select("id")
            .eq("account_id", account_id)
            .gte("created_at", start)
            .lt("created_at", end)
            .limit(500)
            .execute()
        )
        return len(_rows(res))
    except Exception:
        return 0


def _limit_state(account_id: str) -> Dict[str, Any]:
    paid = _is_paid(account_id)
    used = _daily_attempt_count(account_id)
    limit = None if paid else QUIZ_FREE_DAILY_LIMIT
    remaining = None if paid else max(0, QUIZ_FREE_DAILY_LIMIT - used)
    return {
        "paid": paid,
        "daily_limit": limit,
        "attempts_today": used,
        "remaining_today": remaining,
        "limit_reached": False if paid else used >= QUIZ_FREE_DAILY_LIMIT,
    }


def _category_alias(value: Any) -> str:
    norm = _norm(value)
    aliases = {
        "cit": "Company Tax",
        "company": "Company Tax",
        "company tax": "Company Tax",
        "company income tax": "Company Tax",
        "paye": "PAYE",
        "personal income tax": "PAYE",
        "vat": "VAT",
        "wht": "WHT",
        "withholding": "WHT",
        "withholding tax": "WHT",
        "records": "Records",
        "record": "Records",
        "deadlines": "Deadlines",
        "deadline": "Deadlines",
        "penalties": "Penalties",
        "penalty": "Penalties",
        "audit": "Audit",
        "assessment": "Assessment",
    }
    return aliases.get(norm, _clean(value))


def get_quiz_categories() -> Dict[str, Any]:
    categories: set[str] = set()
    try:
        res = _sb().table("tax_quiz_questions").select("category").eq("is_active", True).limit(500).execute()
        for row in _rows(res):
            category = _clean(row.get("category"))
            if category:
                categories.add(category)
    except Exception:
        pass
    for row in FALLBACK_QUIZ_BANK:
        category = _clean(row.get("category"))
        if category:
            categories.add(category)
    ordered = sorted(categories or {"PAYE", "VAT", "Company Tax", "WHT", "Records", "Deadlines"})
    return {"ok": True, "categories": ordered, "version": WEB_QUIZ_SERVICE_VERSION}


def _question_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    short = _clean(row.get("short_explanation") or row.get("explain"))
    premium = _clean(row.get("premium_explanation") or short)
    return {
        "source": "db",
        "db_id": _clean(row.get("id")),
        "id": _clean(row.get("question_code") or row.get("id")),
        "question_code": _clean(row.get("question_code") or row.get("id")),
        "category": _clean(row.get("category") or "General"),
        "difficulty": _clean(row.get("difficulty") or "basic"),
        "question": _clean(row.get("question")),
        "short_explanation": short,
        "premium_explanation": premium,
        "source_reference": _clean(row.get("source_reference") or "Naija Tax Guide quiz bank"),
    }


def _load_question_pool(category: str = "") -> List[Dict[str, Any]]:
    category = _category_alias(category)
    db_rows: List[Dict[str, Any]] = []
    try:
        query = (
            _sb()
            .table("tax_quiz_questions")
            .select("id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active,created_at")
            .eq("is_active", True)
        )
        if category:
            query = query.eq("category", category)
        res = query.limit(250).execute()
        db_rows = [row for row in _rows(res) if _clean(row.get("question"))]
    except Exception:
        db_rows = []
    questions = [_question_from_row(row) for row in db_rows]
    if questions:
        return questions
    fallback = [dict(row) for row in FALLBACK_QUIZ_BANK]
    if category:
        wanted = _norm(category)
        filtered = [row for row in fallback if _norm(row.get("category")) == wanted]
        return filtered or fallback
    return fallback


def _load_db_options(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    db_id = _clean(question.get("db_id"))
    if not db_id:
        return []
    try:
        res = (
            _sb()
            .table("tax_quiz_options")
            .select("id,option_code,option_text,is_correct,created_at")
            .eq("question_id", db_id)
            .limit(20)
            .execute()
        )
        rows = _rows(res)
    except Exception:
        rows = []
    options: List[Dict[str, Any]] = []
    for row in rows:
        text = _clean(row.get("option_text"))
        if not text:
            continue
        options.append(
            {
                "option_id": _clean(row.get("id") or row.get("option_code") or text),
                "option_text": text,
                "is_correct": bool(row.get("is_correct")),
                "source_code": _clean(row.get("option_code")),
            }
        )
    return options


def _load_static_options(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = question.get("options")
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        correct = _clean(question.get("answer") or question.get("correct")).upper()[:1]
        out = []
        for key, value in raw.items():
            label = _clean(key).upper()[:1]
            text = _clean(value)
            if label and text:
                out.append({"option_id": f"{_clean(question.get('id'))}_{label}", "option_text": text, "is_correct": label == correct, "source_code": label})
        return out
    return []


def _randomized_payload(question: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    options = _load_db_options(question) if question.get("source") == "db" else []
    if not options:
        options = _load_static_options(question)
    if not options:
        options = [
            {"option_id": f"{_clean(question.get('id'))}_A", "option_text": "True", "is_correct": True, "source_code": "A"},
            {"option_id": f"{_clean(question.get('id'))}_B", "option_text": "False", "is_correct": False, "source_code": "B"},
        ]
    rng = random.SystemRandom()
    shuffled = list(options)
    rng.shuffle(shuffled)
    labels = ["A", "B", "C", "D"]
    display = []
    correct_label = ""
    correct_option_id = ""
    for label, option in zip(labels, shuffled[:4]):
        option_id = _clean(option.get("option_id") or option.get("source_code") or label)
        option_text = _clean(option.get("option_text"))
        if not option_text:
            continue
        item = {"label": label, "option_id": option_id, "text": option_text}
        if reveal:
            item["is_correct"] = bool(option.get("is_correct"))
        display.append(item)
        if bool(option.get("is_correct")):
            correct_label = label
            correct_option_id = option_id
    if not correct_label and display:
        correct_label = display[0]["label"]
        correct_option_id = display[0]["option_id"]
    return {
        **question,
        "options": display,
        "correct_label": correct_label,
        "correct_option_id": correct_option_id,
    }


def _public_question(payload: Dict[str, Any], limit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "question": {
            "id": payload.get("id"),
            "db_id": payload.get("db_id"),
            "question_code": payload.get("question_code"),
            "category": payload.get("category"),
            "difficulty": payload.get("difficulty"),
            "question": payload.get("question"),
            "options": payload.get("options") or [],
            "source_reference": payload.get("source_reference"),
        },
        "limit": limit,
        "version": WEB_QUIZ_SERVICE_VERSION,
    }


def get_quiz_question(account_id: str, category: str = "") -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required", "message": "Please log in to take the quiz."}
    limit = _limit_state(account_id)
    if limit.get("limit_reached"):
        return {"ok": False, "error": "daily_quiz_limit_reached", "message": f"Free users can take {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily. Paid users get unlimited attempts.", "limit": limit}
    pool = _load_question_pool(category)
    question = random.SystemRandom().choice(pool) if pool else dict(FALLBACK_QUIZ_BANK[0])
    return _public_question(_randomized_payload(question, reveal=False), limit)


def _find_question_by_id_or_code(question_id: str = "", question_code: str = "") -> Optional[Dict[str, Any]]:
    question_id = _clean(question_id)
    question_code = _clean(question_code)
    if question_id:
        try:
            res = _sb().table("tax_quiz_questions").select("id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active").eq("id", question_id).limit(1).execute()
            rows = _rows(res)
            if rows:
                return _question_from_row(rows[0])
        except Exception:
            pass
    if question_code:
        try:
            res = _sb().table("tax_quiz_questions").select("id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active").eq("question_code", question_code).limit(1).execute()
            rows = _rows(res)
            if rows:
                return _question_from_row(rows[0])
        except Exception:
            pass
    for row in FALLBACK_QUIZ_BANK:
        if question_code and _clean(row.get("id")) == question_code:
            return dict(row)
        if question_id and _clean(row.get("id")) == question_id:
            return dict(row)
    return None


def _safe_insert_attempt(payload: Dict[str, Any]) -> Dict[str, Any]:
    payloads = [
        dict(payload),
        {k: v for k, v in payload.items() if k not in {"metadata", "selected_option_id", "correct_option_id"}},
        {k: v for k, v in payload.items() if k in {"account_id", "question_id", "question_code", "category", "status", "created_at", "channel"}},
    ]
    errors: List[str] = []
    seen: set[str] = set()
    for candidate in payloads:
        cleaned = {k: v for k, v in candidate.items() if v is not None and v != ""}
        sig = repr(sorted(cleaned.keys()))
        if sig in seen:
            continue
        seen.add(sig)
        ok, resp, err = _safe_exec(_sb().table("tax_quiz_attempts").insert(cleaned))
        if ok:
            rows = _rows(resp)
            return {"ok": True, "attempt": rows[0] if rows else cleaned}
        errors.append(str(err))
    return {"ok": False, "errors": errors[:3]}


def submit_quiz_answer(account_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required", "message": "Please log in to take the quiz."}
    limit = _limit_state(account_id)
    if limit.get("limit_reached"):
        return {"ok": False, "error": "daily_quiz_limit_reached", "message": f"Free users can take {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily. Paid users get unlimited attempts.", "limit": limit}

    question = _find_question_by_id_or_code(_clean(body.get("question_id") or body.get("db_id")), _clean(body.get("question_code") or body.get("id")))
    if not question:
        return {"ok": False, "error": "question_not_found", "message": "Quiz question could not be found. Please load a new question."}

    payload = _randomized_payload(question, reveal=True)
    selected_label = _clean(body.get("selected_label") or body.get("answer")).upper()[:1]
    selected_option_id = _clean(body.get("selected_option_id"))
    if not selected_label and selected_option_id:
        for opt in payload.get("options") or []:
            if _clean(opt.get("option_id")) == selected_option_id:
                selected_label = _clean(opt.get("label")).upper()[:1]
                break
    selected_option = None
    for opt in payload.get("options") or []:
        if selected_option_id and _clean(opt.get("option_id")) == selected_option_id:
            selected_option = opt
            break
        if selected_label and _clean(opt.get("label")).upper()[:1] == selected_label:
            selected_option = opt
            break
    if not selected_option:
        return {"ok": False, "error": "invalid_answer", "message": "Please select one of the available options."}

    is_correct = bool(selected_option.get("is_correct"))
    correct_option = next((opt for opt in payload.get("options") or [] if bool(opt.get("is_correct"))), None)
    now_iso = _now_iso()
    attempt_payload = {
        "account_id": account_id,
        "question_id": _clean(question.get("db_id")) or None,
        "question_code": _clean(question.get("question_code") or question.get("id")),
        "category": _clean(question.get("category") or "General"),
        "status": "answered",
        "channel": "web",
        "selected_answer": _clean(selected_option.get("label")),
        "selected_option_id": _clean(selected_option.get("option_id")),
        "correct_option_id": _clean((correct_option or {}).get("option_id")),
        "is_correct": is_correct,
        "answered_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
        "metadata": {"source": _clean(question.get("source") or "db"), "difficulty": _clean(question.get("difficulty") or "basic")},
    }
    attempt = _safe_insert_attempt(attempt_payload)
    new_limit = _limit_state(account_id)
    return {
        "ok": True,
        "is_correct": is_correct,
        "selected": {"label": selected_option.get("label"), "text": selected_option.get("text")},
        "correct": {"label": (correct_option or {}).get("label"), "text": (correct_option or {}).get("text")},
        "explanation": _clean(question.get("short_explanation")) or "Review the rule and try another question.",
        "premium_explanation": _clean(question.get("premium_explanation") or question.get("short_explanation")),
        "source_reference": question.get("source_reference") or "Naija Tax Guide quiz bank",
        "attempt": attempt,
        "limit": new_limit,
    }


def get_quiz_score(account_id: str) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required"}
    start, end = _today_bounds()
    rows: List[Dict[str, Any]] = []
    try:
        res = (
            _sb()
            .table("tax_quiz_attempts")
            .select("id,is_correct,status,category,created_at")
            .eq("account_id", account_id)
            .gte("created_at", start)
            .lt("created_at", end)
            .limit(500)
            .execute()
        )
        rows = _rows(res)
    except Exception:
        rows = []
    attempts = len(rows)
    correct = len([r for r in rows if r.get("is_correct") is True])
    wrong = max(0, attempts - correct)
    limit = _limit_state(account_id)
    return {"ok": True, "score": {"attempts_today": attempts, "correct_today": correct, "wrong_today": wrong}, "limit": limit, "version": WEB_QUIZ_SERVICE_VERSION}
