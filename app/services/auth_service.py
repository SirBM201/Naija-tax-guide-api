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


def get_user_from_session_cookie() -> Dict[str, Any] | None:
    """
    Get user from session cookie (ntg_session or Flask session)
    This is the primary authentication method for this app
    """
    try:
        # Check if we have user info in Flask session
        user_id = session.get("user_id")
        user_email = session.get("user_email")
        account_id = session.get("account_id")
        
        if user_id:
            logger.info(f"User found in Flask session: {user_id}")
            return {
                "id": user_id,
                "email": user_email,
                "account_id": account_id or user_id,
            }
        
        # Check for Supabase session in session
        sb_session = session.get("sb:session")
        if sb_session and isinstance(sb_session, dict):
            user = sb_session.get("user")
            if user:
                logger.info(f"User found in Supabase session: {user.get('id')}")
                return user
        
        # Check if we can get user from the ntg_session cookie via Supabase
        # The ntg_session cookie contains the session ID
        ntg_session = request.cookies.get("ntg_session")
        if ntg_session:
            logger.info(f"Found ntg_session cookie: {ntg_session[:20]}...")
            # Try to validate this session with Supabase
            sb = supabase()
            try:
                # Attempt to get session from Supabase using the cookie value
                # This assumes ntg_session is a valid Supabase access token
                res = sb.auth.get_user(ntg_session)
                if res and res.user:
                    logger.info(f"User authenticated via ntg_session cookie: {res.user.id}")
                    return {
                        "id": res.user.id,
                        "email": res.user.email,
                        "account_id": res.user.id,
                    }
            except Exception as e:
                logger.debug(f"Could not validate ntg_session with Supabase: {e}")
        
        return None
    except Exception as e:
        logger.warning(f"Error reading session: {e}")
        return None


def get_user_from_bearer_token(token: str) -> Dict[str, Any] | None:
    """Validate Bearer token with Supabase"""
    sb = supabase()
    try:
        res = sb.auth.get_user(token)
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
    1. Flask session (user_id stored in session)
    2. ntg_session cookie (Supabase session)
    3. Bearer token from Authorization header
    
    Returns user dict with at minimum 'id' field, or None if not authenticated.
    """
    # Method 1: Try Flask session first (most common for this app)
    user = get_user_from_session_cookie()
    if user:
        logger.debug("User authenticated via session cookie")
        return user
    
    # Method 2: Try Bearer token
    token = get_bearer_token()
    if token:
        user = get_user_from_bearer_token(token)
        if user:
            logger.debug("User authenticated via Bearer token")
            return user
        logger.debug("Bearer token present but validation failed")
    
    logger.warning("No valid authentication found for request")
    logger.debug(f"Request headers: {dict(request.headers)}")
    logger.debug(f"Request cookies: {list(request.cookies.keys()) if request.cookies else 'None'}")
    
    return None


def require_auth() -> Dict[str, Any]:
    """
    Helper function for routes that require authentication.
    Returns user dict if authenticated, None if not.
    """
    return get_current_user()
