from __future__ import annotations

from typing import List, Dict, Any


def generate_grounded_answer(
    *,
    question: str,
    lang: str,
    candidates: List[Dict[str, Any]],
) -> str:
    # Replace this with your current OpenAI call.
    # Keep grounding strict.
    basis = []
    for c in candidates[:3]:
        ans = str(c.get("answer") or "").strip()
        if ans:
            basis.append(ans)

    joined_basis = "\n\n".join(basis) if basis else "No trusted basis available."

    return (
        f"Question: {question}\n\n"
        f"Grounded basis:\n{joined_basis}\n\n"
        f"Answer:\n"
        f"Based on the trusted material above, here is the best guidance for this Nigerian tax question."
    )
