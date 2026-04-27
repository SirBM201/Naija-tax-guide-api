from typing import Any, Dict, Optional
from flask import request, session, g
import logging

logger = logging.getLogger(__name__)


def get_current_user() -> Dict[str, Any] | None:
    """
    Get currently authenticated user from Flask session.
    """
    try:
        # First check if user is in g (set by before_request)
        if hasattr(g, 'user') and g.user:
            logger.info(f"User found in g: {g.user.get('id')}")
            return g.user
        
        # Check Flask session directly
        user_id = session.get("user_id")
        if user_id:
            logger.info(f"User found in session: {user_id}")
            return {
                "id": user_id,
                "email": session.get("user_email"),
                "account_id": session.get("account_id") or user_id,
            }
        
        logger.warning("No authenticated user found")
        return None
        
    except Exception as e:
        logger.error(f"Error getting current user: {e}")
        return None


def require_auth() -> Optional[Dict[str, Any]]:
    """Helper for routes that require authentication."""
    return get_current_user()
