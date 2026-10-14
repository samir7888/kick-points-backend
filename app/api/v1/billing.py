"""Billing router: Dodo checkout, portal, status, webhooks."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.models import Subscription, User, WebhookEvent
from app.db.session import get_db_session
from app.integrations.dodo.client import (
    DodoAPIError,
    DodoClient,
    verify_webhook_signature,
)
from app.services.plans import is_premium

router = APIRouter(tags=["billing"])
log = get_logger(__name__)
settings = get_settings()


@router.post(
    "/billing/checkout",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("billing_checkout", limit=5, window=60)),
    ],
)
async def create_checkout(
    user: User = Depends(get_current_user),
) -> dict:
    client = DodoClient()
    data = await client.create_checkout(user_id=user.id, customer_email=user.email)
    checkout_url = data.get("checkout_url")
    if not isinstance(checkout_url, str) or not checkout_url:
        raise DodoAPIError("checkout response")
    return {"checkout_url": checkout_url}


@router.post(
    "/billing/portal",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("billing_portal", limit=5, window=60)),
    ],
)
async def billing_portal(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.updated_at.desc())
    )
    sub = result.scalars().first()
    if not sub:
        return {"portal_url": None, "message": "No active subscription found"}
    if not sub.dodo_customer_id:
        raise DodoAPIError("portal customer")
    client = DodoClient()
    data = await client.create_portal(sub.dodo_customer_id)
    portal_url = data.get("link")
    if not isinstance(portal_url, str) or not portal_url:
        raise DodoAPIError("portal response")
    return {"portal_url": portal_url}


@router.get("/billing/status")
async def billing_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.updated_at.desc())
    )
    sub = result.scalars().first()

    return {
        "plan": "premium" if is_premium(user) else "free",
        "premium_until": user.premium_until.isoformat() if user.premium_until else None,
        "cancel_at_period_end": sub.cancel_at_period_end if sub else False,
        "status": sub.status if sub else "none",
        "price_display": settings.premium_price_display,
    }


@router.post(
    "/webhooks/dodo",
    summary="Receive a Dodo Payments webhook",
    description=(
        "Verifies the raw request signature, deduplicates by provider webhook ID, "
        "and queues processing. Invalid signatures return 401."
    ),
    responses={
        200: {"description": "Webhook accepted or already processed"},
        400: {"description": "Malformed webhook payload or missing headers"},
        401: {"description": "Webhook signature verification failed"},
    },
)
async def dodo_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    webhook_id: str | None = Header(None, alias="webhook-id"),
    webhook_signature: str | None = Header(None, alias="webhook-signature"),
    webhook_timestamp: str | None = Header(None, alias="webhook-timestamp"),
) -> Response:
    """Dodo Payments webhook handler.
    Rules: read raw body → verify signature → idempotency check → process.
    """
    payload_bytes = await request.body()

    # 1. Verify signature
    if not verify_webhook_signature(
        payload_bytes,
        webhook_id,
        webhook_timestamp,
        webhook_signature,
        settings.dodo_webhook_secret,
    ):
        log.warning("dodo_webhook_bad_signature")
        return Response(status_code=401)

    try:
        payload = json.loads(payload_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(status_code=400)

    if not isinstance(payload, dict):
        return Response(status_code=400)
    event_id = webhook_id or ""
    event_type = str(payload.get("type") or "")

    if not event_id or not event_type or not isinstance(payload.get("data"), dict):
        return Response(status_code=400)

    async with db.begin():
        insert_statement = (
            pg_insert(WebhookEvent)
            if db.get_bind().dialect.name == "postgresql"
            else sqlite_insert(WebhookEvent)
        )
        inserted_id = await db.scalar(
            insert_statement.values(
                id=event_id,
                type=event_type,
                payload=payload,
                received_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.id])
            .returning(WebhookEvent.id)
        )
        event = await db.get(WebhookEvent, event_id)
        if event is None:
            raise RuntimeError("Webhook receipt disappeared after insertion")
        should_enqueue = inserted_id is not None or event.processed_at is None

    if not should_enqueue:
        return Response(status_code=200)

    from arq import create_pool

    from app.workers.settings import WorkerSettings

    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        await pool.enqueue_job(
            "webhook_retry_task",
            event_id,
            _job_id=f"dodo-webhook:{event_id}",
        )
    finally:
        await pool.close()

    return Response(status_code=200)
