"""Validate a kick.com site session token (the ``Authorization`` header value).

Kick.com's own web client authenticates its private API with a session token
(``Authorization: Bearer <user_id>|<session>``). That token is separate from
the OAuth access token granted at login — Kick rejects OAuth tokens on these
``/api/v2`` endpoints with 401.

This module probes a lightweight authenticated endpoint (the channel-points
lookup, which returns the token holder's own balance) so callers can tell a
working token from an expired one at save time without placing a vote.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

VALID = "valid"
INVALID = "invalid"
UNKNOWN = "unknown"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# A well-known channel used to probe the authenticated points endpoint: it
# returns the token holder's points on 200 and 401 "Unauthenticated." when the
# token is missing, expired, or invalid.
_PROBE_SLUG = "kick"

_REJECTED_MESSAGE = (
    "Kick rejected this token. It may be expired — open kick.com, "
    "sign back in, and copy a fresh Authorization header."
)


async def validate_kick_session_token(token: str) -> dict[str, str | None]:
    """Check a site session token against kick.com.

    Returns ``{"status": "valid" | "invalid" | "unknown", "message": str | None}``
    where ``message`` is only set when the token was clearly rejected.
    """
    clean = token.strip()
    if not clean:
        return {"status": INVALID, "message": _REJECTED_MESSAGE}

    settings = get_settings()
    url = f"https://kick.com/api/v2/channels/{_PROBE_SLUG}/points"
    headers = {
        "Authorization": f"Bearer {clean}",
        "Accept": "application/json",
        "Referer": "https://kick.com/",
        "User-Agent": _BROWSER_UA,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.kick_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        log.warning(
            "kick_token_validate_transport_failed", error_type=type(exc).__name__
        )
        return {"status": UNKNOWN, "message": None}

    if response.status_code == 200:
        return {"status": VALID, "message": None}
    if response.status_code == 401:
        log.info("kick_token_validate_rejected", status_code=response.status_code)
        return {"status": INVALID, "message": _REJECTED_MESSAGE}

    log.warning(
        "kick_token_validate_unexpected", status_code=response.status_code
    )
    return {"status": UNKNOWN, "message": None}
