"""Auth router: Kick OAuth login, callback, logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.config import get_settings
from app.core.errors import UnauthorizedError
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit_ip
from app.db.models import User
from app.db.session import get_db_session
from app.integrations.kick.oauth import exchange_code, fetch_kick_user
from app.services.audit import record_audit
from app.services.auth import (
    consume_oauth_state,
    create_oauth_state,
    create_session,
    delete_session,
    upsert_user_from_kick,
)

router = APIRouter(tags=["auth"])
log = get_logger(__name__)
settings = get_settings()


@router.get("/auth/kick/login", dependencies=[Depends(rate_limit_ip)])
async def kick_login(db: AsyncSession = Depends(get_db_session)) -> RedirectResponse:
    """Redirect user to Kick OAuth authorize endpoint."""
    async with db.begin():
        _, _, authorize_url = await create_oauth_state(db)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/auth/kick/callback", dependencies=[Depends(rate_limit_ip)])
async def kick_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Handle Kick OAuth callback."""
    async with db.begin():
        try:
            code_verifier = await consume_oauth_state(state, db)
        except ValueError as exc:
            log.warning("invalid_oauth_state", error=str(exc))
            raise UnauthorizedError(str(exc)) from exc

        redirect_uri = (
            settings.kick_redirect_uri
            or f"{settings.api_url}/api/v1/auth/kick/callback"
        )
        token_data = await exchange_code(code, code_verifier, redirect_uri)
        kick_user_data = await fetch_kick_user(token_data["access_token"])

        user = await upsert_user_from_kick(kick_user_data, token_data, db)

        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        session_token = await create_session(user.id, db, user_agent=ua, ip=ip)
        await record_audit(
            db,
            "login",
            user_id=user.id,
            detail={"kick_user_id": user.kick_user_id},
            ip=ip,
        )

    dashboard_url = f"{settings.site_url.rstrip('/')}/dashboard"
    resp = RedirectResponse(url=dashboard_url, status_code=302)
    resp.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        domain=(
            settings.cookie_domain if settings.cookie_domain != "localhost" else None
        ),
        max_age=settings.session_ttl_days * 86400,
    )
    log.info("user_logged_in", user_id=user.id, kick_user_id=user.kick_user_id)
    return resp


@router.post(
    "/auth/logout",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("logout", limit=10, window=60)),
    ],
)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    token = request.cookies.get(settings.session_cookie_name, "")
    await delete_session(token, db)
    await record_audit(
        db,
        "logout",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}
