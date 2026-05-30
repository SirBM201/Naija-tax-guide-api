# app/routes/promo.py
from __future__ import annotations

from typing import Any, Dict
from flask import Blueprint, jsonify, redirect, request

from app.services.promo_service import (
    PROMO_SERVICE_VERSION,
    build_promo_links,
    track_promo_event,
    validate_promo_code,
)

bp = Blueprint("promo", __name__)
PROMO_ROUTE_VERSION = "2026-05-29-batch35A-promo-hub-signup-only"


def _clean(value: Any) -> str:
    return str(value or "").strip()


@bp.get("/promo/health")
def promo_health():
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "service_version": PROMO_SERVICE_VERSION,
        "rule": "Promo code is captured at signup/onboarding, not at payment form.",
        "expected_urls": [
            "/api/promo/health",
            "/api/promo/validate/TAXWITHBM",
            "/api/promo/hub/TAXWITHBM",
            "/api/promo/track?code=TAXWITHBM&platform=website",
            "/api/promo/track-and-go/TAXWITHBM/website",
            "/api/promo/track-and-go/TAXWITHBM/whatsapp",
            "/api/promo/track-and-go/TAXWITHBM/telegram",
        ],
    }), 200


@bp.get("/promo/validate/<code>")
def promo_validate(code: str):
    result = validate_promo_code(code)
    links = build_promo_links(code)
    return jsonify({**result, "route_version": PROMO_ROUTE_VERSION, "links": links}), 200


@bp.get("/promo/hub/<code>")
def promo_hub(code: str):
    validation = validate_promo_code(code)
    links = build_promo_links(code)
    tracking = track_promo_event(
        promo_code=code,
        event_type="promo_hub_view",
        selected_platform=request.args.get("source") or request.args.get("platform"),
        landing_url=request.url,
        request_obj=request,
        metadata={"query": dict(request.args), "route": "/api/promo/hub/<code>"},
    )
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "promo_code": links["code"],
        "valid": bool(validation.get("valid")),
        "validation": validation,
        "links": links,
        "tracking": {"ok": bool(tracking.get("ok")), "non_blocking": True},
    }), 200


@bp.route("/promo/track", methods=["GET", "POST"])
def promo_track():
    if request.method == "POST":
        body: Dict[str, Any] = request.get_json(silent=True) or {}
        code = body.get("code") or body.get("promo_code")
        platform = body.get("platform") or body.get("selected_platform")
        event_type = body.get("event_type") or "promo_platform_select"
    else:
        body = {}
        code = request.args.get("code") or request.args.get("promo_code")
        platform = request.args.get("platform") or request.args.get("selected_platform")
        event_type = request.args.get("event_type") or "promo_platform_select"

    validation = validate_promo_code(code)
    links = build_promo_links(code)
    selected = _clean(platform).lower() or "website"
    destinations = {
        "web": links["website"],
        "website": links["website"],
        "signup": links["website"],
        "whatsapp": links["whatsapp"],
        "wa": links["whatsapp"],
        "telegram": links["telegram"],
        "tg": links["telegram"],
    }
    destination = destinations.get(selected, links["website"])
    tracking = track_promo_event(
        promo_code=code,
        event_type=event_type,
        selected_platform=selected,
        landing_url=destination,
        request_obj=request,
        metadata={"query": dict(request.args), "body": body, "route": "/api/promo/track"},
    )
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "promo_code": links["code"],
        "valid": bool(validation.get("valid")),
        "selected_platform": selected,
        "destination": destination,
        "tracking": {"ok": bool(tracking.get("ok")), "non_blocking": True},
    }), 200


@bp.get("/promo/track-and-go/<code>/<platform>")
def promo_track_and_go(code: str, platform: str):
    links = build_promo_links(code)
    selected = _clean(platform).lower() or "website"
    destinations = {
        "web": links["website"],
        "website": links["website"],
        "signup": links["website"],
        "whatsapp": links["whatsapp"],
        "wa": links["whatsapp"],
        "telegram": links["telegram"],
        "tg": links["telegram"],
    }
    destination = destinations.get(selected, links["website"])
    track_promo_event(
        promo_code=code,
        event_type="promo_platform_click",
        selected_platform=selected,
        landing_url=destination,
        request_obj=request,
        metadata={"query": dict(request.args), "route": "/api/promo/track-and-go/<code>/<platform>"},
    )
    return redirect(destination, code=302)
