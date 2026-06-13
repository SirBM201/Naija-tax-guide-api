from __future__ import annotations

import re


ASK_ANSWER_FORMAT_PATCH_VERSION = "2026-06-14-v2-route-and-service-empty-section-cleanup"

_EMPTY_LIST_LINE_RE = re.compile(r"^\s*(?:\d+[\.)]|[•\-])\s*$")
_SECTION_LABEL_RE = re.compile(
    r"^\s*(Direct\s+answer|Short\s+answer|Answer|Key\s+points|What\s+to\s+do|Next\s+steps|Note)\s*:\s*$",
    re.IGNORECASE,
)


def _is_empty_list_body(lines: list[str]) -> bool:
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty:
        return True
    return all(_EMPTY_LIST_LINE_RE.match(line) for line in non_empty)


def _clean_empty_answer_sections(text: str) -> str:
    clean = str(text or "").replace("\r\n", "\n")
    if not clean.strip():
        return ""

    lines = clean.split("\n")
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        label_match = _SECTION_LABEL_RE.match(line)

        if label_match:
            label = label_match.group(1).lower().replace(" ", "")
            j = i + 1
            body_lines: list[str] = []

            while j < len(lines) and not _SECTION_LABEL_RE.match(lines[j]):
                body_lines.append(lines[j])
                j += 1

            if label in {"keypoints", "whattodo", "nextsteps"} and _is_empty_list_body(body_lines):
                i = j
                continue

            out.append(line.rstrip())
            for body_line in body_lines:
                if _EMPTY_LIST_LINE_RE.match(body_line):
                    continue
                out.append(body_line.rstrip())
            i = j
            continue

        if not _EMPTY_LIST_LINE_RE.match(line):
            out.append(line.rstrip())
        i += 1

    clean = "\n".join(out)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _patch_ask_service() -> None:
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


def _patch_ask_route() -> None:
    try:
        from app.routes import ask as ask_route
    except Exception:
        return

    original = getattr(ask_route, "_clean_repeated_answer_labels", None)
    if original is None or getattr(original, "_ntg_format_patch_applied", False):
        return

    def patched(value):
        cleaned = original(value)
        return _clean_empty_answer_sections(cleaned)

    patched._ntg_format_patch_applied = True  # type: ignore[attr-defined]
    ask_route._clean_repeated_answer_labels = patched


def apply_ask_answer_format_patch() -> None:
    _patch_ask_service()
    _patch_ask_route()
