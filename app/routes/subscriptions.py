# add to imports
from app.services.subscriptions_service import (
    activate_subscription_now,
    debug_read_subscription,
    debug_expose_subscription_health,   # NEW
)

# add this route
@bp.get("/_debug/subscription_health")
def debug_subscription_health():
    """
    Admin debugger exposer that returns:
    - whether supabase client is valid
    - whether RPC functions exist
    - whether table select works
    - recommended SQL actions (auto-generated hints)
    Query: ?account_id=<uuid> (optional)
    """
    req_id = str(uuid.uuid4())

    if not _is_admin(request):
        return _fail(401, "unauthorized", req_id=req_id, root_cause={"where": "admin_guard", "message": "Missing/invalid X-Admin-Key", "request_id": req_id})

    try:
        account_id = (request.args.get("account_id") or "").strip() or None
        result = debug_expose_subscription_health(account_id)
        # Ensure route request_id is present (keep your style consistent)
        result.setdefault("request_id", req_id)
        return jsonify(result), (200 if result.get("ok") else 500)

    except Exception as e:
        return _fail(
            500,
            "internal_error",
            req_id=req_id,
            root_cause=_rootcause(
                "routes.subscriptions.debug_subscription_health",
                e,
                req_id=req_id,
                hint="Unexpected exception in debugger exposer.",
            ),
        )
