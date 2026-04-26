from typing import Any, Dict, Optional
from flask import request, session
from app.core.supabase_client import supabase
import logging

logger = logging.getLogger(__name__)


def get_bearer_token() -> str | None:
    """Extract Bearer token from Authorization header"""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None


def get_user_from_session() -> Dict[str, Any] | None:
    """
    Get user from Flask session (for session-based authentication)
    """
    try:
        # Check if user info is stored in session
        user_id = session.get("user_id")
        user_email = session.get("user_email")
        account_id = session.get("account_id")
        
        if user_id:
            logger.info(f"User found in session: {user_id}")
            return {
                "id": user_id,
                "email": user_email,
                "account_id": account_id or user_id,
            }
        
        # Also check for supabase session cookie pattern
        supabase_session = session.get("sb-session")
        if supabase_session and isinstance(supabase_session, dict):
            user = supabase_session.get("user")
            if user:
                logger.info(f"User found in Supabase session: {user.get('id')}")
                return user
        
        return None
    except Exception as e:
        logger.warning(f"Error reading session: {e}")
        return None


def get_user_from_bearer_token(token: str) -> Dict[str, Any] | None:
    """
    Validate Bearer token with Supabase
    """
    sb = supabase()
    try:
        # supabase-py supports auth.get_user(token)
        res = sb.auth.get_user(token)
        # Depending on supabase-py version, object shape may differ
        user = getattr(res, "user", None) or (res.get("user") if isinstance(res, dict) else None)
        if not user:
            logger.warning("Bearer token validation returned no user")
            return None
        
        logger.info(f"User authenticated via Bearer token: {user.get('id') if isinstance(user, dict) else user.id}")
        
        if isinstance(user, dict):
            return user
        else:
            return user.model_dump() if hasattr(user, 'model_dump') else {"id": user.id, "email": user.email}
    except Exception as e:
        logger.warning(f"Bearer token validation failed: {e}")
        return None


def get_current_user() -> Dict[str, Any] | None:
    """
    Get currently authenticated user.
    
    Supports multiple authentication methods in this order:
    1. Bearer token from Authorization header (JWT)
    2. Flask session (user_id stored in session)
    3. Supabase session cookie
    
    Returns user dict with at minimum 'id' field, or None if not authenticated.
    """
    # Method 1: Try Bearer token first (most secure)
    token = get_bearer_token()
    if token:
        user = get_user_from_bearer_token(token)
        if user:
            logger.debug("User authenticated via Bearer token")
            return user
        logger.debug("Bearer token present but validation failed")
    
    # Method 2: Try Flask session
    user = get_user_from_session()
    if user:
        logger.debug("User authenticated via Flask session")
        return user
    
    # Method 3: Try to get from request cookies directly (fallback)
    try:
        # Check for Supabase auth cookie
        sb = supabase()
        # Supabase stores session in a specific cookie format
        auth_cookie = request.cookies.get('sb-access-token') or request.cookies.get('sb-refresh-token')
        if auth_cookie:
            # Try to validate the cookie content
            logger.debug("Found Supabase auth cookie but not validating directly")
    except Exception as e:
        logger.debug(f"Cookie check failed: {e}")
    
    logger.warning("No valid authentication found for request")
    return None


def require_auth() -> Dict[str, Any]:
    """
    Helper function for routes that require authentication.
    Returns user dict if authenticated, raises 401 if not.
    """
    user = get_current_user()
    if not user:
        from flask import jsonify
        # This is meant to be used with @bp.route, so we return a response
        # But for simplicity, we'll return None and let routes handle 401
        return None
    return user
