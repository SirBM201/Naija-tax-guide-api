@bp.get("/billing/verify")
def billing_verify():
    """
    Verify a reference (after Paystack redirect).
    SECURE VERSION:
      - requires auth
      - prevents someone else from verifying another user's reference
    GET /billing/verify?reference=...
    """
    # 1) Require auth
    requester_account_id, debug = get_account_id_from_request(request)
    if not requester_account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    # 2) Extract reference
    reference = (request.args.get("reference") or "").strip()
    if not reference:
        return jsonify({"ok": False, "error": "missing_reference"}), 400

    # 3) Verify with Paystack
    try:
        ps = verify_transaction(reference)
    except Exception as e:
        return jsonify({"ok": False, "error": "paystack_verify_failed", "root_cause": repr(e)}), 400

    tx = (ps or {}).get("data") or {}
    status = (tx.get("status") or "").strip().lower()
    tx_id = str(tx.get("id") or "") or None
    metadata = tx.get("metadata") or {}

    plan_code = (metadata.get("plan_code") or "").strip().lower()
    tx_account_id = (metadata.get("account_id") or "").strip()

    _store_paystack_event(event_id=tx_id, event_type="verify", reference=reference, payload=ps)

    # 4) If not success, do not activate
    if status != "success":
        return jsonify(
            {
                "ok": True,
                "paid": False,
                "status": status,
                "reference": reference,
                "data": tx,
                "account_id": requester_account_id,
            }
        ), 200

    # 5) Must have metadata
    if not plan_code or not tx_account_id:
        return jsonify(
            {
                "ok": False,
                "error": "missing_metadata",
                "metadata": metadata,
                "reference": reference,
                "account_id": requester_account_id,
            }
        ), 400

    # 6) SECURITY CHECK: tx metadata must match the logged-in user
    if tx_account_id != requester_account_id:
        return jsonify(
            {
                "ok": False,
                "error": "forbidden_reference_owner_mismatch",
                "reference": reference,
                "account_id": requester_account_id,
            }
        ), 403

    # 7) Validate plan
    plan = get_plan(plan_code)
    if not plan:
        return jsonify({"ok": False, "error": "plan_not_found", "plan_code": plan_code}), 404

    # 8) Activate subscription
    sub = _upsert_user_subscription(
        account_id=requester_account_id,
        plan_code=plan_code,
        duration_days=int(plan["duration_days"]),
        provider="paystack",
        provider_ref=reference,
    )

    return jsonify(
        {
            "ok": True,
            "paid": True,
            "reference": reference,
            "subscription": sub,
            "plan": plan,
            "account_id": requester_account_id,
        }
    ), 200
