from __future__ import annotations

import os
from typing import List, Dict, Any


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _build_basis(candidates: List[Dict[str, Any]]) -> str:
    blocks = []

    for idx, c in enumerate(candidates[:3], start=1):
        answer = _safe_str(c.get("answer"))
        topic = _safe_str(c.get("topic"))
        intent_type = _safe_str(c.get("intent_type"))
        jurisdiction = _safe_str(c.get("jurisdiction"))
        trust_score = c.get("trust_score")
        similarity = c.get("similarity")
        match_type = _safe_str(c.get("match_type"))

        if not answer:
            continue

        blocks.append(
            "\n".join(
                [
                    f"Candidate {idx}:",
                    f"- topic: {topic}",
                    f"- intent_type: {intent_type}",
                    f"- jurisdiction: {jurisdiction}",
                    f"- trust_score: {trust_score}",
                    f"- similarity: {similarity}",
                    f"- match_type: {match_type}",
                    f"- answer: {answer}",
                ]
            )
        )

    return "\n\n".join(blocks) if blocks else "No trusted basis available."


def generate_grounded_answer(
    *,
    question: str,
    lang: str,
    candidates: List[Dict[str, Any]],
    grounding_context: str | None = None,
) -> str:
    """
    Current safe implementation.

    If you already have real OpenAI wiring later, this function can be upgraded
    without changing ask_service again.

    For now it produces a controlled grounded synthesis from the top candidates.
    """

    basis = _build_basis(candidates)

    # Optional future toggle for real provider use
    use_live_provider = str(os.getenv("USE_LIVE_GROUNDED_AI", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if use_live_provider:
        # Keep this explicit so the current file remains safe even before
        # provider wiring is added.
        # Replace this section later with your actual OpenAI/API call.
        pass

    if not candidates:
        return (
            "I do not have enough trusted Nigerian tax material to generate a safe answer for that question yet."
        )

    answer_lines = [
        "Based on the strongest available Nigerian tax guidance in the system, here is the best supported answer:",
        "",
        f"Question: {question}",
        "",
        "Grounded basis:",
        basis,
    ]

    if grounding_context:
        answer_lines.extend(
            [
                "",
                "Grounding context:",
                grounding_context,
            ]
        )

    answer_lines.extend(
        [
            "",
            "Practical guidance:",
            "Use the strongest matching Nigerian tax rule or approved answer above as the basis for your next step. If the situation involves registration, penalties, filing dates, or multi-branch structuring, verify the exact compliance context before acting.",
        ]
    )

    return "\n".join(answer_lines).strip()
