@bp.post("/support/ticket")
def create_ticket():
    """Alternative endpoint for creating a support ticket (simpler version)"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return _unauthorized(auth_debug)
    
    body = _safe_json()
    
    # Map simpler fields to your existing structure
    simplified_body = {
        "full_name": body.get("fullName") or body.get("name"),
        "contact_email": body.get("email") or body.get("contactEmail"),
        "issue_type": body.get("category") or body.get("issueType") or "general",
        "priority": "normal",
        "subject": body.get("subject") or "Support Request",
        "message": body.get("message") or body.get("description"),
        "channel": "web",
    }
    
    # Override request.json temporarily and call existing submit_support
    original_json = request.get_json
    request.get_json = lambda silent=True: simplified_body
    
    try:
        return submit_support()
    finally:
        request.get_json = original_json


@bp.get("/support/health")
def support_health():
    """Health check endpoint"""
    to_email = _support_to_email()
    return (
        jsonify(
            {
                "ok": True,
                "route_group": "support",
                "mail_ready": bool(to_email),
                "support_to_email": to_email or None,
                "endpoints": ["/support", "/support/tickets", "/support/tickets/<id>", "/support/tickets/<id>/reply"]
            }
        ),
        200,
    )


@bp.get("/support/stats")
def support_stats():
    """Get support statistics for the authenticated user"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return _unauthorized(auth_debug)
    
    try:
        # Get statistics about user's tickets
        tickets, err = _list_tickets_for_account(account_id, limit=1000)
        if err:
            return _fail(error=err.get("error") or "stats_failed", status=500)
        
        open_count = len([t for t in tickets if t.get("status") == "open"])
        in_progress_count = len([t for t in tickets if t.get("status") == "in_progress"])
        closed_count = len([t for t in tickets if t.get("status") == "closed"])
        
        return jsonify({
            "ok": True,
            "stats": {
                "total": len(tickets),
                "open": open_count,
                "in_progress": in_progress_count,
                "closed": closed_count
            }
        }), 200
    except Exception as e:
        return _fail(error="stats_failed", root_cause=str(e), status=500)


@bp.delete("/support/tickets/<ticket_id>/close")
def close_ticket(ticket_id: str):
    """Close a support ticket"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return _unauthorized(auth_debug)
    
    ticket_id = (ticket_id or "").strip()
    if not ticket_id:
        return _fail(error="ticket_id_required", status=400)
    
    ticket, err = _find_ticket_for_account(account_id, ticket_id)
    if err:
        return _fail(error=err.get("error") or "ticket_lookup_failed", status=500)
    
    if not ticket:
        return _fail(error="ticket_not_found", status=404)
    
    # Update ticket status to closed
    try:
        _sb().table("support_tickets") \
            .update({"status": "closed"}) \
            .eq("id", ticket["id"]) \
            .execute()
        
        refreshed_ticket, _ = _find_ticket_for_account(account_id, ticket_id)
        
        return jsonify({
            "ok": True,
            "message": "Ticket closed successfully",
            "ticket": refreshed_ticket or ticket
        }), 200
    except Exception as e:
        return _fail(error="close_failed", root_cause=str(e), status=500)
