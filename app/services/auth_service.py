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
        
        # Check if we can get user from the ntg_session cookie
        ntg_session = request.cookies.get("ntg_session")
        if ntg_session:
            logger.info(f"Found ntg_session cookie: {ntg_session[:20]}...")
            
            # supabase is already a Client object - use it directly
            try:
                # Try to validate the session with Supabase
                # The ntg_session cookie might be a Supabase access token
                res = supabase.auth.get_user(ntg_session)
                if res and hasattr(res, 'user') and res.user:
                    user_data = res.user
                    logger.info(f"User authenticated via ntg_session cookie: {user_data.id}")
                    return {
                        "id": user_data.id,
                        "email": user_data.email,
                        "account_id": user_data.id,
                    }
            except Exception as e:
                logger.warning(f"Could not validate ntg_session with Supabase: {e}")
        
        return None
    except Exception as e:
        logger.warning(f"Error reading session: {e}")
        return None


def get_user_from_bearer_token(token: str) -> Dict[str, Any] | None:
    """Validate Bearer token with Supabase"""
    try:
        # supabase is already a Client object - use it directly
        res = supabase.auth.get_user(token)
        if res and hasattr(res, 'user') and res.user:
            user_data = res.user
            logger.info(f"User authenticated via Bearer token: {user_data.id}")
            return {
                "id": user_data.id,
                "email": user_data.email,
                "account_id": user_data.id,
            }
        else:
            logger.warning("Bearer token validation returned no user")
            return None
    except Exception as e:
        logger.warning(f"Bearer token validation failed: {e}")
        return None


def get_current_user() -> Dict[str, Any] | None:
    """
    Get currently authenticated user.
    
    Supports multiple authentication methods in this order:
    1. ntg_session cookie (Supabase session) - PRIMARY for this app
    2. Flask session (user_id stored in session)
    3. Bearer token from Authorization header
    
    Returns user dict with at minimum 'id' field, or None if not authenticated.
    """
    # Method 1: Try ntg_session cookie first (most common for this app)
    user = get_user_from_session_cookie()
    if user:
        logger.debug("User authenticated via session cookie")
        return user
    
    # Method 2: Try Flask session user_id
    user_id = session.get("user_id")
    if user_id:
        logger.info(f"User found in Flask session (fallback): {user_id}")
        return {
            "id": user_id,
            "email": session.get("user_email"),
            "account_id": session.get("account_id") or user_id,
        }
    
    # Method 3: Try Bearer token
    token = get_bearer_token()
    if token:
        user = get_user_from_bearer_token(token)
        if user:
            logger.debug("User authenticated via Bearer token")
            return user
        logger.debug("Bearer token present but validation failed")
    
    # Log detailed debug info for troubleshooting
    logger.warning("No valid authentication found for request")
    logger.debug(f"Request path: {request.path}")
    logger.debug(f"Request cookies: {list(request.cookies.keys()) if request.cookies else 'None'}")
    logger.debug(f"Authorization header present: {bool(request.headers.get('Authorization'))}")
    
    return None


def require_auth() -> Optional[Dict[str, Any]]:
    """
    Helper function for routes that require authentication.
    Returns user dict if authenticated, None if not.
    """
    return get_current_user()
