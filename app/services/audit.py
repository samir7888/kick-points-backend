"""Small helper for recording security- and billing-sensitive actions."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def record_audit(
    db: AsyncSession,
    action: str,
    *,
    user_id: int | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            detail=detail or {},
            ip=ip,
        )
    )
    await db.flush()
