# app/services/reminder_service.py
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)


def subscribe_to_reminders(account_id: str, channel: str, contact: str) -> Dict[str, Any]:
    """Subscribe a user to tax deadline reminders"""
    try:
        # Check if subscription exists
        result = supabase.table("reminder_subscriptions")\
            .select("id")\
            .eq("account_id", account_id)\
            .eq("channel", channel)\
            .execute()
        
        if result.data and len(result.data) > 0:
            # Update existing subscription
            supabase.table("reminder_subscriptions")\
                .update({
                    "active": True,
                    "contact": contact,
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("id", result.data[0]["id"])\
                .execute()
            return {"ok": True, "message": "✅ You are already subscribed to reminders!"}
        
        # Create new subscription
        supabase.table("reminder_subscriptions").insert({
            "account_id": account_id,
            "channel": channel,
            "contact": contact,
            "active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        
        return {"ok": True, "message": "✅ You will now receive tax deadline reminders!"}
        
    except Exception as e:
        logger.error(f"Failed to subscribe: {e}")
        return {"ok": False, "message": "Failed to subscribe. Please try again later."}


def unsubscribe_from_reminders(account_id: str, channel: str) -> Dict[str, Any]:
    """Unsubscribe a user from reminders"""
    try:
        result = supabase.table("reminder_subscriptions")\
            .select("id")\
            .eq("account_id", account_id)\
            .eq("channel", channel)\
            .execute()
        
        if result.data and len(result.data) > 0:
            supabase.table("reminder_subscriptions")\
                .update({
                    "active": False,
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("id", result.data[0]["id"])\
                .execute()
            return {"ok": True, "message": "❌ You have been unsubscribed from reminders."}
        
        return {"ok": True, "message": "You were not subscribed to reminders."}
        
    except Exception as e:
        logger.error(f"Failed to unsubscribe: {e}")
        return {"ok": False, "message": "Failed to unsubscribe. Please try again later."}


def get_user_reminder_status(account_id: str, channel: str) -> Dict[str, Any]:
    """Check if a user is subscribed to reminders"""
    try:
        result = supabase.table("reminder_subscriptions")\
            .select("active")\
            .eq("account_id", account_id)\
            .eq("channel", channel)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return {"subscribed": result.data[0].get("active", False)}
        return {"subscribed": False}
        
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        return {"subscribed": False}


def get_subscribers() -> List[Dict[str, Any]]:
    """Get all active subscribers"""
    try:
        result = supabase.table("reminder_subscriptions")\
            .select("account_id, channel, contact")\
            .eq("active", True)\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        logger.error(f"Failed to get subscribers: {e}")
        return []
