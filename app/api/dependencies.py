"""FastAPI dependencies for authentication and database sessions."""

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppException, ErrorCode
from ..core.security import verify_origin
from ..db.models import User
from ..db.session import get_db_session
from ..services.auth import AuthService


async def get_current_user(
    request: Request,
    session_token: Optional[str] = Cookie(None, alias="session"),
    db_session: AsyncSession = Depends(get_db_session),
) -> Optional[User]:
    """Get current authenticated user from session cookie."""
    if not session_token:
        return None

    auth_service = AuthService(db_session)
    user = await auth_service.get_user_by_session_token(session_token)

    return user


async def require_authenticated_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """Require an authenticated user, raise 401 if not authenticated."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return user


async def require_admin_user(
    user: User = Depends(require_authenticated_user),
) -> User:
    """Require an admin user, raise 403 if not admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


async def verify_csrf_protection(request: Request) -> None:
    """Verify CSRF protection for state-changing requests."""
    try:
        verify_origin(request)
    except HTTPException:
        # Re-raise with better error message
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid request origin",
        )
