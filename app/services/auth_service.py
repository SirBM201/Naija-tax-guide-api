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
            
            # Get the Supabase client (not as a callable)
            sb = supabase
            
            # Check if sb has auth attribute
            if hasattr(sb, 'auth') and hasattr(sb.auth, 'get_user'):
                try:
                    # Try to validate the session with Supabase
                    res = sb.auth.get_user(ntg_session)
                    if res and hasattr(res, 'user') and res.user:
                        logger.info(f"User authenticated via ntg_session cookie: {res.user.id}")
                        return {
                            "id": res.user.id,
                            "email": res.user.email,
                            "account_id": res.user.id,
                        }
                except Exception as e:
                    logger.warning(f"Could not validate ntg_session with Supabase: {e}")
            else:
                logger.warning("Supabase client does not have expected auth methods")
        
        return None
    except Exception as e:
        logger.warning(f"Error reading session: {e}")
        return None


def get_user_from_bearer_token(token: str) -> Dict[str, Any] | None:
    """Validate Bearer token with Supabase"""
    sb = supabase
    try:
        if hasattr(sb, 'auth') and hasattr(sb.auth, 'get_user'):
            res = sb.auth.get_user(token)
            user = getattr(res, "user", None)
            if not user:
                logger.warning("Bearer token validation returned no user")
                return None
            
            logger.info(f"User authenticated via Bearer token: {user.id if hasattr(user, 'id') else 'unknown'}")
            
            return {
                "id": user.id if hasattr(user, 'id') else user.get('id'),
                "email": user.email if hasattr(user, 'email') else user.get('email'),
            }
        else:
            logger.warning("Supabase client does not have auth.get_user method")
            return None
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
    # Method 1: Try Flask session first
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
    
    # Log detailed debug info
    logger.debug(f"Request headers: {dict(request.headers)}")
    logger.debug(f"Request cookies: {list(request.cookies.keys()) if request.cookies else 'None'}")
    
    return None


def require_auth() -> Optional[Dict[str, Any]]:
    """
    Helper function for routes that require authentication.
    Returns user dict if authenticated, None if not.
    """
    return get_current_user()
