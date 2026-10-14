"""Manual QR / bank-transfer payment router.

Two personas:

- **Users** (authenticated) scan the billing QR, pay, and upload a transfer
  screenshot from `/billing`. The record starts as ``pending``.
- **Admins** review submissions on the password-protected `/payments` page and
  either confirm (granting one month of Premium directly on the user row, no
  Dodo subscription) or reject them.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.cache import invalidate_user
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, UnauthorizedError, ValidationError
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.models import QrPayment, User
from app.db.session import get_db_session
from app.services.audit import record_audit
from app.services.plans import is_premium, reconcile_channels

router = APIRouter(tags=["payments"])
log = get_logger(__name__)

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PREMIUM_MONTH_DAYS = 30


async def _admin_password_dependency(
    password: str | None = Header(
        None,
        alias="x-payments-password",
        description="Password for the /payments review page",
    ),
) -> None:
    """Reject requests that don't carry the payments-review password."""
    settings = get_settings()
    expected = settings.payments_admin_password or ""
    if not password or not expected or not secrets.compare_digest(password, expected):
        raise UnauthorizedError("Incorrect payments password")


def _serialize(payment: QrPayment) -> dict[str, Any]:
    """Shape a QrPayment for API responses (proof served separately)."""
    return {
        "id": payment.id,
        "user_id": payment.user_id,
        "username": payment.username,
        "email": payment.email,
        "reference": payment.reference,
        "amount": float(payment.amount) if payment.amount is not None else None,
        "status": payment.status,
        "created_at": payment.created_at.isoformat()
        if payment.created_at
        else None,
        "decided_at": payment.decided_at.isoformat()
        if payment.decided_at
        else None,
    }


async def _load_payment(payment_id: int, db: AsyncSession) -> QrPayment:
    payment = await db.get(QrPayment, payment_id)
    if payment is None:
        raise NotFoundError("Payment submission not found")
    return payment


@router.post(
    "/billing/qr-payments",
    summary="Submit a payment proof for the scanned QR",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("qr_payment_submit", limit=5, window=3600)),
    ],
)
async def submit_qr_payment(
    request: Request,
    proof: UploadFile = File(..., description="Transfer screenshot"),
    reference: str | None = Form(None, description="Optional transfer reference"),
    amount: float | None = Form(None, description="Optional reported amount"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Save the user's proof and start a pending review."""
    if is_premium(user):
        raise ValidationError(
            "You already have a Premium account; no payment is needed.",
            details={"field": "plan"},
        )

    file_name = proof.filename or ""
    extension = Path(file_name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            "Please upload a screenshot as PNG, JPG, WebP or GIF.",
            details={"field": "proof"},
        )

    content = await proof.read()
    if len(content) > settings.max_upload_body_bytes:
        raise ValidationError(
            "That screenshot is too large. Keep it under "
            f"{settings.max_upload_body_bytes // (1024 * 1024)} MB.",
            details={"field": "proof"},
        )

    upload_dir = Path(settings.qr_payment_dir)
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("qr_payment_upload_dir_failed", error_type=type(exc).__name__)
        raise ValidationError("Could not save your proof. Please try again.") from exc

    stored_name = f"{uuid.uuid4().hex}{extension}"
    target = upload_dir / stored_name
    target.write_bytes(content)

    payment = QrPayment(
        user_id=user.id,
        username=user.username,
        email=user.email,
        reference=reference or None,
        amount=amount,
        proof_filename=stored_name,
        status=QrPayment.PENDING,
    )
    db.add(payment)
    await record_audit(
        db,
        "qr_payment_submitted",
        user_id=user.id,
        detail={"proof": stored_name, "reference": reference or None},
        ip=request.client.host if request.client else None,
    )
    await db.commit()

    log.info("qr_payment_submitted", user_id=user.id, payment_id=payment.id)
    return {"ok": True, "id": payment.id, "status": payment.status}


@router.get("/billing/qr-payments")
async def list_my_qr_payments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return the current user's QR payment submissions, newest first."""
    result = await db.execute(
        select(QrPayment)
        .where(QrPayment.user_id == user.id)
        .order_by(QrPayment.created_at.desc())
    )
    payments = result.scalars().all()
    return {"items": [_serialize(payment) for payment in payments]}


@router.get(
    "/qr-payments",
    summary="List QR payment submissions (admin, password-gated)",
    dependencies=[Depends(_admin_password_dependency)],
)
async def list_qr_payments(
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return every QR payment submission, newest first, for the /payments page."""
    result = await db.execute(select(QrPayment).order_by(QrPayment.created_at.desc()))
    payments = result.scalars().all()
    return {"items": [_serialize(payment) for payment in payments]}


@router.get(
    "/qr-payments/{payment_id}/proof",
    summary="Serve a payment proof image (admin, password-gated)",
    dependencies=[Depends(_admin_password_dependency)],
)
async def qr_payment_proof(
    payment_id: int,
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Return the stored screenshot for a submission."""
    payment = await _load_payment(payment_id, db)
    target = Path(settings.qr_payment_dir) / payment.proof_filename
    if not target.is_file():
        raise NotFoundError("Proof image is missing")
    return FileResponse(target)


@router.post(
    "/qr-payments/{payment_id}/confirm",
    summary="Confirm a QR payment (admin, password-gated)",
    dependencies=[
        Depends(check_csrf),
        Depends(_admin_password_dependency),
    ],
)
async def confirm_qr_payment(
    request: Request,
    payment_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Confirm the transfer and grant one month of Premium."""
    payment = await _load_payment(payment_id, db)
    if payment.status == QrPayment.CONFIRMED:
        return {"ok": True, "status": payment.status}

    result = await db.execute(select(User).where(User.id == payment.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("The submitting account no longer exists")

    now = utcnow()
    base = (
        user.premium_until
        if user.premium_until and user.premium_until > now
        else now
    )
    user.premium_until = base + timedelta(days=PREMIUM_MONTH_DAYS)
    user.premium_grace_until = None

    payment.status = QrPayment.CONFIRMED
    payment.decided_at = utcnow()
    payment.decided_by = request.headers.get("x-payments-admin") or "admin"

    await record_audit(
        db,
        "qr_payment_confirmed",
        user_id=payment.user_id,
        detail={"payment_id": payment.id, "premium_until": user.premium_until.isoformat()},
        ip=request.client.host if request.client else None,
    )
    await reconcile_channels(user, db)
    await db.commit()
    await invalidate_user(payment.user_id)

    from app.api.v1.ops import publish_sse

    await publish_sse(payment.user_id, "billing_updated", {"plan": "premium"})

    log.info(
        "qr_payment_confirmed",
        user_id=payment.user_id,
        payment_id=payment.id,
    )
    return {
        "ok": True,
        "status": payment.status,
        "premium_until": user.premium_until.isoformat(),
    }


@router.post(
    "/qr-payments/{payment_id}/reject",
    summary="Reject a QR payment (admin, password-gated)",
    dependencies=[
        Depends(check_csrf),
        Depends(_admin_password_dependency),
    ],
)
async def reject_qr_payment(
    request: Request,
    payment_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Reject the submission; Premium is not granted."""
    payment = await _load_payment(payment_id, db)
    if payment.status == QrPayment.REJECTED:
        return {"ok": True, "status": payment.status}

    payment.status = QrPayment.REJECTED
    payment.decided_at = utcnow()
    payment.decided_by = request.headers.get("x-payments-admin") or "admin"

    await record_audit(
        db,
        "qr_payment_rejected",
        user_id=payment.user_id,
        detail={"payment_id": payment.id},
        ip=request.client.host if request.client else None,
    )
    await db.commit()

    from app.api.v1.ops import publish_sse

    # Let the user's open billing page stop showing the proof as pending.
    await publish_sse(
        payment.user_id,
        "billing_updated",
        {"plan": "free", "reason": "qr_payment_rejected"},
    )

    log.info(
        "qr_payment_rejected",
        user_id=payment.user_id,
        payment_id=payment.id,
    )
    return {"ok": True, "status": payment.status}
