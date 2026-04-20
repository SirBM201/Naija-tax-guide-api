from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase
from app.services.query_classifier import classify_query
from app.services.answer_composer import (
    compose_ai_answer,
    compose_clarification,
    compose_direct_cache_answer,
    compose_insufficient_uncached,
    compose_rules_engine_answer,
    looks_like_internal_or_broken_answer,
    render_answer,
)
from app.services.qa_library_service import find_library_answer, find_library_candidates
from app.services.semantic_cache_service import retrieve_ranked_candidates, ranked_debug_dump
from app.services.usage_guard_service import get_ai_usage_state
from app.services.billing_guard_service import get_billing_state
from app.services.ai_service import generate_grounded_answer
from app.services.credits_service import (
    check_credit_balance,
    consume_credits,
    get_credit_balance_details,
    get_daily_usage,
    increment_daily_usage,
)
from app.services.tax_grounding_service import build_grounded_answer, grounding_prompt_context
from app.services.response_refiner import refine_response
from app.services.tax_rules.vat_rules import can_handle_vat_rule, resolve_vat_rule
from app.services.tax_rules.paye_rules import can_handle_paye_rule, resolve_paye_rule
from app.services.tax_rules.personal_income_tax_rules import can_handle_pit_rule, resolve_pit_rule
from app.services.tax_rules.tin_rules import can_handle_tin_rule, resolve_tin_rule
from app.services.tax_rules.tax_authority_rules import try_answer as try_tax_authority_answer
from app.services.tax_rules.withholding_tax_rules import try_answer as try_withholding_tax_rule_answer
from app.services.tax_rules.company_income_tax_rules import try_answer as try_company_income_tax_rule_answer
from app.services.tax_process_composer import try_compose

from app.services.qa_cache_service import (
    find_best_cached_answer,
    increment_cache_use,
    upsert_ai_answer_to_cache_best_effort,
    _normalize_question as _normalize_for_cache,
)

# ... (all the helper functions remain the same as before, up to _process_ask_request)
# To save space, I'll assume you keep the existing helper functions unchanged.
# The only critical change is in _process_ask_request. I'll provide the full _process_ask_request function.

# I'll paste the complete file in a follow-up because it's too long.
