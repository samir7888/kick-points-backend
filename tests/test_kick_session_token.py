"""Tests for kick.com site session-token validation."""

import pytest
import respx
from httpx import Response

from app.integrations.kick.session_token import validate_kick_session_token

PROBE_URL = "https://kick.com/api/v2/channels/kick/points"


@pytest.mark.asyncio
async def test_valid_token_returns_valid() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(PROBE_URL).mock(
            return_value=Response(200, json={"data": {"points": 4218}, "message": "OK"})
        )
        result = await validate_kick_session_token("12345|abcdef")
        assert result["status"] == "valid"
        assert result["message"] is None
        # Token is forwarded as a Bearer credential.
        assert (
            route.calls[0].request.headers["authorization"]
            == "Bearer 12345|abcdef"
        )


@pytest.mark.asyncio
async def test_rejected_token_returns_invalid_with_message() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(PROBE_URL).mock(
            return_value=Response(401, json={"message": "Unauthenticated."})
        )
        result = await validate_kick_session_token("expired|session")
        assert result["status"] == "invalid"
        assert result["message"] and "rejected" in result["message"].lower()


@pytest.mark.asyncio
async def test_unexpected_status_returns_unknown() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(PROBE_URL).mock(return_value=Response(503))
        result = await validate_kick_session_token("12345|abcdef")
        assert result["status"] == "unknown"
        assert result["message"] is None


@pytest.mark.asyncio
async def test_blank_token_returns_invalid_without_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(PROBE_URL).mock(return_value=Response(200))
        result = await validate_kick_session_token("   ")
        assert result["status"] == "invalid"
