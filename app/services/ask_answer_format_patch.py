from __future__ import annotations

import re


ASK_ANSWER_FORMAT_PATCH_VERSION = "2026-06-14-v1-remove-empty-key-points"

_EMPTY_KEY_POINTS_RE = re.compile(
    r"(?:\n\s*)?Key\s+points\s*:\s*(?:\n\s*(?:\d+[\.)]|[•\-])\s*)+(?=\n\s*(?:Note\s*:|What\s+to\s+do\s*:|Next\s+steps\s*:)|\s*$)",
    re.IGNORECASE,
)

_EMPTY_LIST_LINE_RE = re.compile(r"^\s*(?:\d+[\.)]|[•\-])\s*$")


def _clean_empty_answer_sections(text: str) -> str:
    clean = str(text or "").replace("\r\n", "\n")
    clean = _EMPTY_KEY_POINTS_RE.sub("\n", clean)

    lines: list[str] = []
    for line in clean.split("\n"):
        if _EMPTY_LIST_LINE_RE.match(line):
            continue
        lines.append(line.rstrip())

    clean = "\n".join(lines)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def apply_ask_answer_format_patch() -> None:
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    original = getattr(svc, "_ensure_professional_answer_shape", None)
    if original is None or getattr(original, "_ntg_format_patch_applied", False):
        return

    def patched(answer: str, question: str = "") -> str:
        shaped = original(answer, question)
        return _clean_empty_answer_sections(shaped)

    patched._ntg_format_patch_applied = True  # type: ignore[attr-defined]
    svc._ensure_professional_answer_shape = patched
