"""FastAPI dependency utilities."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError
from app.core.rate_limit import rate_limit_user
from app.core.security import verify_origin
from app.db.models import User
from app.db.session import get_db_session
from app.services.auth import get_user_from_session


async def get_current_user(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Extract and validate session cookie, return User."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise UnauthorizedError()
    user = await get_user_from_session(token, db)
    if not user:
        raise UnauthorizedError()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        domain=(
            settings.cookie_domain if settings.cookie_domain != "localhost" else None
        ),
        max_age=settings.session_ttl_days * 86400,
    )
    return user


async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


async def check_csrf(
    request: Request, settings: Settings = Depends(get_settings)
) -> None:
    """CSRF protection: check Origin/Referer against allowed origins."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    try:
        verify_origin(request)
    except HTTPException as exc:
        raise HTTPException(status_code=403, detail="CSRF check failed") from exc


def rate_limit_for_user(action: str, limit: int = 30, window: int = 60):
    """Build a dependency that rate-limits an authenticated user action."""

    async def dependency(user: User = Depends(get_current_user)) -> None:
        await rate_limit_user(user.id, action, limit=limit, window=window)

    return dependency
