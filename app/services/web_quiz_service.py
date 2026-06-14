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


WEB_QUIZ_SERVICE_VERSION = "2026-06-14-v3-display-order-q5-parity"
QUIZ_FREE_DAILY_LIMIT = 12
RECENT_QUESTION_AVOID_LIMIT = 8

FALLBACK_QUIZ_BANK: List[Dict[str, Any]] = [
    {
        "id": "web_q_paye_1",
        "category": "PAYE",
        "difficulty": "basic",
        "question": "Which Nigerian tax is normally deducted from employee salaries by the employer?",
        "options": [
            {"option_id": "web_q_paye_1_A", "option_text": "PAYE deducted from employment income", "is_correct": True, "source_code": "A"},
            {"option_id": "web_q_paye_1_B", "option_text": "VAT charged on taxable sales invoices", "is_correct": False, "source_code": "B"},
            {"option_id": "web_q_paye_1_C", "option_text": "Company Income Tax on company profits", "is_correct": False, "source_code": "C"},
            {"option_id": "web_q_paye_1_D", "option_text": "Withholding Tax on qualifying supplier payments", "is_correct": False, "source_code": "D"},
        ],
        "short_explanation": "PAYE is deducted from employment income and remitted by the employer to the relevant State Internal Revenue Service.",
        "premium_explanation": "PAYE means Pay-As-You-Earn. It is a payroll tax mechanism where the employer deducts tax from salaries and remits it to the relevant State Internal Revenue Service.",
        "source_reference": "Naija Tax Guide fallback quiz bank",
    },
    {
        "id": "web_q_vat_1",
        "category": "VAT",
        "difficulty": "basic",
        "question": "VAT in Nigeria is best described as which type of tax?",
        "options": [
            {"option_id": "web_q_vat_1_A", "option_text": "A consumption tax on taxable supplies of goods and services", "is_correct": True, "source_code": "A"},
            {"option_id": "web_q_vat_1_B", "option_text": "A tax on company taxable profit after adjustments", "is_correct": False, "source_code": "B"},
            {"option_id": "web_q_vat_1_C", "option_text": "A payroll tax deducted from employment income", "is_correct": False, "source_code": "C"},
            {"option_id": "web_q_vat_1_D", "option_text": "A deduction at source from qualifying payments", "is_correct": False, "source_code": "D"},
        ],
        "short_explanation": "VAT is a consumption tax charged on many taxable goods and services.",
        "premium_explanation": "VAT is charged on taxable supplies. Businesses may charge output VAT and claim allowable input VAT before remitting net VAT where applicable.",
        "source_reference": "Naija Tax Guide fallback quiz bank",
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


def _parse_dt(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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


def _as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = _norm(value)
    if text in {"true", "1", "yes", "y", "active", "enabled"}:
        return True
    if text in {"false", "0", "no", "n", "inactive", "disabled"}:
        return False
    return None


def _plan_family(row: Dict[str, Any]) -> str:
    text = _norm(row.get("plan_code") or row.get("plan") or row.get("tier") or row.get("package_code") or row.get("name"))
    if "business" in text:
        return "business"
    if "professional" in text or text.startswith("pro"):
        return "professional"
    if "starter" in text:
        return "starter"
    return "free"


def _subscription_is_active(row: Dict[str, Any]) -> bool:
    status = _norm(row.get("status") or row.get("subscription_status") or row.get("payment_status"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False

    for key in ("is_active", "active", "enabled"):
        explicit = _as_bool(row.get(key))
        if explicit is False:
            return False
        if explicit is True and status in {"", "active", "paid", "successful", "success", "trial", "trialing", "grace", "past_due"}:
            break

    for key in ("expires_at", "current_period_end", "valid_until", "ends_at", "period_end"):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt >= datetime.now(timezone.utc)

    if status:
        return status in {"active", "paid", "successful", "success", "trial", "trialing", "grace", "past_due"}

    return _as_bool(row.get("is_active")) is True or _as_bool(row.get("active")) is True


def _subscription_sort_key(row: Dict[str, Any]) -> str:
    for key in ("updated_at", "created_at", "activated_at", "paid_at", "current_period_start"):
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _subscription_rows(account_id: str) -> List[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return []

    all_rows: List[Dict[str, Any]] = []
    for table in ("user_subscriptions", "subscriptions"):
        for order_col in ("updated_at", "created_at", "id", ""):
            try:
                q = _sb().table(table).select("*").eq("account_id", account_id)
                if order_col:
                    q = q.order(order_col, desc=True)
                res = q.limit(20).execute()
                rows = _rows(res)
                if rows:
                    all_rows.extend(rows)
                    break
            except Exception:
                continue

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in all_rows:
        key = _clean(row.get("id") or repr(sorted(row.items())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return sorted(deduped, key=_subscription_sort_key, reverse=True)


def _is_paid(account_id: str) -> bool:
    account_id = _clean(account_id)
    if not account_id:
        return False

    if has_active_subscription is not None:
        try:
            if bool(has_active_subscription(account_id)):
                return True
        except Exception:
            pass

    rows = _subscription_rows(account_id)
    for row in rows:
        if _plan_family(row) in {"starter", "professional", "business"} and _subscription_is_active(row):
            return True
    return False


def _daily_attempt_count(account_id: str) -> int:
    if not _clean(account_id):
        return 0
    start, end = _today_bounds()
    for with_status in (True, False):
        try:
            q = _sb().table("tax_quiz_attempts").select("id").eq("account_id", account_id)
            if with_status:
                q = q.eq("status", "answered")
            q = q.gte("created_at", start).lt("created_at", end).limit(500)
            return len(_rows(q.execute()))
        except Exception:
            continue
    return 0


def _limit_state(account_id: str) -> Dict[str, Any]:
    paid = _is_paid(account_id)
    used = _daily_attempt_count(account_id)
    return {
        "paid": paid,
        "daily_limit": None if paid else QUIZ_FREE_DAILY_LIMIT,
        "attempts_today": used,
        "remaining_today": None if paid else max(0, QUIZ_FREE_DAILY_LIMIT - used),
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
        "pay as you earn": "PAYE",
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
        "sme": "SME Basics",
        "sme basics": "SME Basics",
    }
    return aliases.get(norm, _clean(value))


def _question_key(question: Dict[str, Any]) -> str:
    return _clean(question.get("question_code") or question.get("id") or question.get("db_id") or question.get("question"))


def _parse_exclude_codes(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = re.split(r"[,\s]+", _clean(value))
    return {_clean(item) for item in raw_items if _clean(item)}


def _recent_question_codes(account_id: str, category: str = "") -> List[str]:
    account_id = _clean(account_id)
    if not account_id:
        return []
    try:
        query = (
            _sb()
            .table("tax_quiz_attempts")
            .select("question_code,category,created_at,status")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(RECENT_QUESTION_AVOID_LIMIT * 3)
        )
        if category:
            query = query.eq("category", category)
        rows = _rows(query.execute())
    except Exception:
        rows = []

    out: List[str] = []
    for row in rows:
        code = _clean(row.get("question_code"))
        if code and code not in out:
            out.append(code)
        if len(out) >= RECENT_QUESTION_AVOID_LIMIT:
            break
    return out


def get_quiz_categories() -> Dict[str, Any]:
    categories: set[str] = set()
    try:
        rows = _rows(_sb().table("tax_quiz_questions").select("category").eq("is_active", True).limit(500).execute())
        for row in rows:
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
        db_rows = [row for row in _rows(query.limit(250).execute()) if _clean(row.get("question"))]
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


def _choose_question(account_id: str, pool: List[Dict[str, Any]], category: str = "", exclude_codes: Any = None) -> Dict[str, Any]:
    if not pool:
        return dict(FALLBACK_QUIZ_BANK[0])

    blocked = set(_recent_question_codes(account_id, _category_alias(category)))
    blocked.update(_parse_exclude_codes(exclude_codes))
    available = [row for row in pool if _question_key(row) not in blocked]
    if not available:
        available = list(pool)
    return dict(random.SystemRandom().choice(available))


def _load_db_options(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    db_id = _clean(question.get("db_id"))
    if not db_id:
        return []
    try:
        rows = _rows(
            _sb()
            .table("tax_quiz_options")
            .select("id,option_code,option_text,is_correct,created_at")
            .eq("question_id", db_id)
            .limit(20)
            .execute()
        )
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


def _canonical_options(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    options = _load_db_options(question) if question.get("source") == "db" else []
    if not options:
        options = _load_static_options(question)
    if not options:
        options = [
            {"option_id": f"{_clean(question.get('id'))}_A", "option_text": "True", "is_correct": True, "source_code": "A"},
            {"option_id": f"{_clean(question.get('id'))}_B", "option_text": "False", "is_correct": False, "source_code": "B"},
        ]
    return options


def _randomized_payload(question: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    options = _canonical_options(question)
    rng = random.SystemRandom()
    shuffled = list(options)
    source_signature = [_clean(opt.get("option_id") or opt.get("source_code")) for opt in shuffled]

    for _ in range(8):
        rng.shuffle(shuffled)
        new_signature = [_clean(opt.get("option_id") or opt.get("source_code")) for opt in shuffled]
        if new_signature != source_signature or len(shuffled) <= 2:
            break

    labels = ["A", "B", "C", "D"]
    display = []
    correct_label = ""
    correct_option_id = ""
    option_order: List[Dict[str, str]] = []

    for label, option in zip(labels, shuffled[:4]):
        option_id = _clean(option.get("option_id") or option.get("source_code") or label)
        option_text = _clean(option.get("option_text"))
        if not option_text:
            continue
        item = {"label": label, "option_id": option_id, "text": option_text}
        if reveal:
            item["is_correct"] = bool(option.get("is_correct"))
        display.append(item)
        option_order.append({"label": label, "option_id": option_id})
        if bool(option.get("is_correct")):
            correct_label = label
            correct_option_id = option_id

    if not correct_label and display:
        correct_label = display[0]["label"]
        correct_option_id = display[0]["option_id"]

    return {
        **question,
        "options": display,
        "option_order": option_order,
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


def get_quiz_question(account_id: str, category: str = "", exclude_codes: Any = None) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required", "message": "Please log in to take the quiz."}
    limit = _limit_state(account_id)
    if limit.get("limit_reached"):
        return {"ok": False, "error": "daily_quiz_limit_reached", "message": f"Free users can take {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily. Paid users get unlimited attempts.", "limit": limit}
    pool = _load_question_pool(category)
    question = _choose_question(account_id, pool, category, exclude_codes)
    return _public_question(_randomized_payload(question, reveal=False), limit)


def _find_question_by_id_or_code(question_id: str = "", question_code: str = "") -> Optional[Dict[str, Any]]:
    question_id = _clean(question_id)
    question_code = _clean(question_code)
    if question_id:
        try:
            rows = _rows(
                _sb()
                .table("tax_quiz_questions")
                .select("id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active")
                .eq("id", question_id)
                .limit(1)
                .execute()
            )
            if rows:
                return _question_from_row(rows[0])
        except Exception:
            pass
    if question_code:
        try:
            rows = _rows(
                _sb()
                .table("tax_quiz_questions")
                .select("id,question_code,category,difficulty,question,short_explanation,premium_explanation,source_reference,is_active")
                .eq("question_code", question_code)
                .limit(1)
                .execute()
            )
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


def _option_order_maps(raw: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    by_id: Dict[str, str] = {}
    by_label: Dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = _clean(item.get("label")).upper()[:1]
            option_id = _clean(item.get("option_id"))
            if label and option_id:
                by_id[option_id] = label
                by_label[label] = option_id
    elif isinstance(raw, dict):
        for key, value in raw.items():
            k = _clean(key)
            v = _clean(value)
            if not k or not v:
                continue
            if len(k) == 1 and k.upper() in {"A", "B", "C", "D"}:
                by_label[k.upper()] = v
                by_id[v] = k.upper()
            else:
                by_id[k] = v.upper()[:1]
                by_label[v.upper()[:1]] = k
    return by_id, by_label


def _displayed_option_maps(raw: Any) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    by_id: Dict[str, Dict[str, str]] = {}
    by_label: Dict[str, Dict[str, str]] = {}
    if not isinstance(raw, list):
        return by_id, by_label
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("label")).upper()[:1]
        option_id = _clean(item.get("option_id"))
        text = _clean(item.get("text") or item.get("option_text"))
        value = {"label": label, "option_id": option_id, "text": text}
        if option_id:
            by_id[option_id] = value
        if label:
            by_label[label] = value
    return by_id, by_label


def _label_for_option(option_id: str, option: Optional[Dict[str, Any]], order_by_id: Dict[str, str], fallback: str = "") -> str:
    option_id = _clean(option_id)
    if option_id and option_id in order_by_id:
        return order_by_id[option_id]
    source = _clean((option or {}).get("source_code")).upper()[:1]
    if source:
        return source
    return _clean(fallback).upper()[:1]


def _safe_insert_attempt(payload: Dict[str, Any]) -> Dict[str, Any]:
    payloads = [
        dict(payload),
        {k: v for k, v in payload.items() if k not in {"metadata", "selected_option_id", "correct_option_id", "displayed_option_order"}},
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

    options = _canonical_options(question)
    options_by_id = {_clean(opt.get("option_id")): opt for opt in options if _clean(opt.get("option_id"))}
    selected_label_from_client = _clean(body.get("selected_label") or body.get("answer")).upper()[:1]
    selected_option_id = _clean(body.get("selected_option_id"))
    selected_text_from_client = _clean(body.get("selected_text") or body.get("selected_option_text"))
    order_by_id, order_by_label = _option_order_maps(body.get("option_order") or body.get("displayed_option_order") or body.get("options_order"))
    display_by_id, display_by_label = _displayed_option_maps(body.get("displayed_options") or body.get("options"))

    if not selected_option_id and selected_label_from_client in order_by_label:
        selected_option_id = order_by_label[selected_label_from_client]

    selected_option = options_by_id.get(selected_option_id)

    if not selected_option and selected_text_from_client:
        wanted = _norm(selected_text_from_client)
        selected_option = next((opt for opt in options if _norm(opt.get("option_text")) == wanted), None)
        if selected_option:
            selected_option_id = _clean(selected_option.get("option_id"))

    if not selected_option and selected_label_from_client:
        # Last-resort compatibility only. Displayed option_order above must win.
        selected_option = next((opt for opt in options if _clean(opt.get("source_code")).upper()[:1] == selected_label_from_client), None)
        if selected_option:
            selected_option_id = _clean(selected_option.get("option_id"))

    if not selected_option:
        return {"ok": False, "error": "invalid_answer", "message": "Please select one of the available options."}

    correct_option = next((opt for opt in options if bool(opt.get("is_correct"))), None)
    correct_option_id = _clean((correct_option or {}).get("option_id"))

    selected_label = selected_label_from_client or _label_for_option(selected_option_id, selected_option, order_by_id, _clean(selected_option.get("source_code")))
    if selected_option_id in order_by_id:
        selected_label = order_by_id[selected_option_id]

    selected_display = display_by_id.get(selected_option_id) or display_by_label.get(selected_label) or {}
    selected_text = _clean(selected_display.get("text")) or selected_text_from_client or _clean(selected_option.get("option_text"))

    correct_label = _label_for_option(correct_option_id, correct_option, order_by_id, _clean((correct_option or {}).get("source_code")))
    correct_display = display_by_id.get(correct_option_id) or display_by_label.get(correct_label) or {}
    correct_text = _clean(correct_display.get("text")) or _clean((correct_option or {}).get("option_text"))

    is_correct = bool(selected_option_id and correct_option_id and selected_option_id == correct_option_id)
    if not selected_option_id or not correct_option_id:
        is_correct = bool(selected_option.get("is_correct"))

    channel = _clean(body.get("channel") or "web").lower() or "web"
    now_iso = _now_iso()
    attempt_payload = {
        "account_id": account_id,
        "question_id": _clean(question.get("db_id")) or None,
        "question_code": _clean(question.get("question_code") or question.get("id")),
        "category": _clean(question.get("category") or "General"),
        "status": "answered",
        "channel": channel,
        "selected_answer": selected_label,
        "selected_option_id": selected_option_id,
        "correct_option_id": correct_option_id,
        "is_correct": is_correct,
        "answered_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
        "metadata": {
            "source": _clean(question.get("source") or "db"),
            "difficulty": _clean(question.get("difficulty") or "basic"),
            "displayed_option_order": order_by_id,
            "displayed_options": body.get("displayed_options") or body.get("options") or [],
            "selected_label_from_client": selected_label_from_client,
            "selected_text_from_client": selected_text_from_client,
        },
    }
    attempt = _safe_insert_attempt(attempt_payload)
    return {
        "ok": True,
        "is_correct": is_correct,
        "selected": {"label": selected_label, "text": selected_text},
        "correct": {"label": correct_label, "text": correct_text},
        "explanation": _clean(question.get("short_explanation")) or "Review the rule and try another question.",
        "premium_explanation": _clean(question.get("premium_explanation") or question.get("short_explanation")),
        "source_reference": question.get("source_reference") or "Naija Tax Guide quiz bank",
        "attempt": attempt,
        "limit": _limit_state(account_id),
    }


def _credit_balance_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        rows = _rows(_sb().table("ai_credit_balances").select("*").eq("account_id", account_id).limit(1).execute())
        if rows:
            return rows[0], None
        return None, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:500]}"


def _credit_column(row: Dict[str, Any]) -> str:
    for col in ("balance", "credits", "credit_balance"):
        if col in row:
            return col
    return "balance"


def _insert_credit_log(payload: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    for table in ("credit_usage_logs", "credit_transactions", "ai_credit_transactions"):
        ok, resp, err = _safe_exec(_sb().table(table).insert(payload))
        if ok:
            return {"ok": True, "table": table, "data": _rows(resp)}
        errors.append(str(err))
    return {"ok": False, "errors": errors[:3], "payload": payload}


def unlock_quiz_detailed_explanation(account_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required", "message": "Please log in to use Q5."}

    question = _find_question_by_id_or_code(_clean(body.get("question_id") or body.get("db_id")), _clean(body.get("question_code") or body.get("id")))
    if not question:
        return {"ok": False, "error": "question_not_found", "message": "Quiz question could not be found. Please answer a new question and try again."}

    explanation = _clean(question.get("premium_explanation") or question.get("short_explanation"))
    if not explanation:
        return {"ok": False, "error": "explanation_not_available", "message": "A saved detailed explanation is not available for this question yet."}

    if not _is_paid(account_id):
        return {"ok": False, "error": "paid_plan_required", "message": "Q5 detailed explanation requires an active paid plan."}

    row, row_error = _credit_balance_row(account_id)
    if not row:
        return {"ok": False, "error": "insufficient_credits", "message": "Q5 costs 1 Usage Credit, but your balance is not enough.", "balance": 0, "lookup_error": row_error}

    col = _credit_column(row)
    try:
        before = int(row.get(col) or 0)
    except Exception:
        before = 0
    if before < 1:
        return {"ok": False, "error": "insufficient_credits", "message": "Q5 costs 1 Usage Credit, but your balance is not enough.", "balance": before}

    after = before - 1
    update_payloads = [
        {col: after, "updated_at": _now_iso()},
        {col: after},
    ]
    update_errors: List[str] = []
    updated = False
    for payload in update_payloads:
        ok, _resp, err = _safe_exec(_sb().table("ai_credit_balances").update(payload).eq("account_id", account_id))
        if ok:
            updated = True
            break
        update_errors.append(str(err))

    if not updated:
        return {"ok": False, "error": "credit_deduction_failed", "message": "Credit deduction failed before Q5 could be unlocked.", "errors": update_errors[:2]}

    reference = f"WEB-Q5-{uuid.uuid4().hex[:12].upper()}"
    log = _insert_credit_log(
        {
            "account_id": account_id,
            "reference": reference,
            "action_code": "ai_quiz_explanation",
            "description": "Q5 detailed saved quiz explanation",
            "channel": _clean(body.get("channel") or "web") or "web",
            "credits_delta": -1,
            "balance_after": after,
            "metadata": {
                "source": "web_quiz_q5_saved_explanation",
                "live_ai_called": False,
                "question_code": _clean(question.get("question_code") or question.get("id")),
                "question_id": _clean(question.get("db_id")),
                "category": _clean(question.get("category") or "General"),
                "balance_before": before,
                "balance_after": after,
            },
            "created_at": _now_iso(),
        }
    )

    return {
        "ok": True,
        "explanation": explanation,
        "source_reference": question.get("source_reference") or "Naija Tax Guide quiz bank",
        "credit": {
            "deducted": True,
            "credits_deducted": 1,
            "balance_before": before,
            "balance_after": after,
            "reference": reference,
            "live_ai_called": False,
            "log": log,
        },
    }


def get_quiz_score(account_id: str) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_required"}
    start, end = _today_bounds()
    rows: List[Dict[str, Any]] = []
    for with_status in (True, False):
        try:
            q = _sb().table("tax_quiz_attempts").select("id,is_correct,status,category,created_at").eq("account_id", account_id)
            if with_status:
                q = q.eq("status", "answered")
            q = q.gte("created_at", start).lt("created_at", end).limit(500)
            rows = _rows(q.execute())
            break
        except Exception:
            continue
    attempts = len(rows)
    correct = len([r for r in rows if r.get("is_correct") is True])
    wrong = max(0, attempts - correct)
    return {"ok": True, "score": {"attempts_today": attempts, "correct_today": correct, "wrong_today": wrong}, "limit": _limit_state(account_id), "version": WEB_QUIZ_SERVICE_VERSION}


def format_quiz_question_for_channel(result: Dict[str, Any]) -> str:
    question = result.get("question") if isinstance(result.get("question"), dict) else {}
    options = question.get("options") if isinstance(question.get("options"), list) else []
    lines = [
        f"🧠 Tax Quiz ({_clean(question.get('category')) or 'General'})",
        "",
        f"Question: {_clean(question.get('question'))}",
        "",
    ]
    for option in options:
        lines.append(f"{_clean(option.get('label'))}. {_clean(option.get('text'))}")
    lines.extend(["", "Reply A, B, C, or D."])
    return "\n".join(lines)
