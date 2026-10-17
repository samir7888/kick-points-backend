"""Billing router: plan status.

Payments are handled out of band via the manual QR / bank-transfer flow in
``app.api.v1.payments``. Premium is granted only when an admin confirms a
submitted payment proof; there is no hosted checkout or external provider.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import User
from app.services.plans import is_premium

router = APIRouter(tags=["billing"])
settings = get_settings()


@router.get("/billing/status")
async def billing_status(
    user: User = Depends(get_current_user),
) -> dict:
    """Return the caller's current plan and Premium expiry."""
    premium = is_premium(user)
    return {
        "plan": "premium" if premium else "free",
        "premium_until": user.premium_until.isoformat() if user.premium_until else None,
        "status": "active" if premium else "none",
        "price_display": settings.premium_price_display,
    }
