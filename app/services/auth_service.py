from typing import Any, Dict, Optional
from flask import request, session
import logging

logger = logging.getLogger(__name__)


def get_current_user() -> Dict[str, Any] | None:
    """
    Get currently authenticated user from Flask session.
    This matches your web_auth system.
    """
    try:
        # Log session state for debugging
        logger.debug(f"Session keys in get_current_user: {list(session.keys()) if session else 'None'}")
        
        # Check if user is in Flask session (set by verify-otp)
        user_id = session.get("user_id")
        user_email = session.get("user_email")
        account_id = session.get("account_id")
        
        if user_id:
            logger.info(f"User authenticated from session: {user_id}")
            return {
                "id": user_id,
                "email": user_email,
                "account_id": account_id or user_id,
            }
        
        logger.debug("No user_id found in session")
        return None
        
    except Exception as e:
        logger.warning(f"Error getting current user: {e}")
        return None


def require_auth() -> Optional[Dict[str, Any]]:
    """Helper for routes that require authentication."""
    return get_current_user()
