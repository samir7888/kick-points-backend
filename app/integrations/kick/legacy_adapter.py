"""Legacy adapter — wraps legacy/src/ code behind the KickGateway Protocol.

Mapping from legacy code to Protocol methods:
  resolve_channel     ← KickClient.get_channel(username)
  get_active_prediction ← KickClient.get_active_prediction(username) + normalization
  get_points_balance  ← KickClient.get_channel_points(username) via the
                        /api/v2/channels/{slug}/points endpoint
  place_vote          ← KickClient.vote_prediction(username, prediction_id, outcome_id, amount)
  listen              ← ChannelPusherListener (wrapped as async generator)

Changes made inside legacy/:
  NONE — the legacy code is imported as-is. We instantiate KickClient with the
  decrypted access token at call time. cloudscraper calls are run in a thread
  pool executor to avoid blocking the async event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.config import get_settings
from app.core.metrics import track_kick_call
from app.integrations.kick.models import (
    ChannelInfo,
    KickSchemaError,
    OutcomeInfo,
    PredictionEvent,
    VoteResult,
)

log = structlog.get_logger(__name__)

# Kick uses a handful of state/status strings for closed predictions. Fold the
# aliases onto the states the rest of the app models.
_STATE_ALIASES = {
    "ENDED": "RESOLVED",
    "FINISHED": "RESOLVED",
    "PAID": "RESOLVED",
    "CANCELED": "CANCELLED",
    "REFUNDED": "CANCELLED",
}


# Lazy import legacy — it may not be importable in all environments
try:
    import os
    import sys

    _legacy_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "legacy")
    if _legacy_path not in sys.path:
        sys.path.insert(0, _legacy_path)
    from src.client import KickClient as _KickClient  # type: ignore[import]
    from src.pusher_listener import ChannelPusherListener as _Pusher  # type: ignore[import]

    _LEGACY_AVAILABLE = True
except Exception as exc:
    log.warning("legacy_import_failed", error=str(exc))
    _LEGACY_AVAILABLE = False


def _utc(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise KickSchemaError("Prediction timestamp is out of range") from exc
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError as exc:
        raise KickSchemaError("Prediction timestamp is invalid") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _normalize_prediction(raw: dict[str, Any], channel_slug: str) -> PredictionEvent:
    """Convert a legacy/Kick API prediction dict to PredictionEvent.
    Raises KickSchemaError if required fields are missing.
    """
    try:
        pred_id = str(raw.get("id", "") or "")
        title = raw.get("title", "")
        outcomes_raw = raw.get("outcomes") or []
        state = str(raw.get("state") or raw.get("status") or "ACTIVE").upper()
        # Kick reports some closed predictions with aliases we don't model
        # directly; fold them onto the states the app understands.
        state = _STATE_ALIASES.get(state, state)

        if not pred_id:
            raise KickSchemaError("Missing prediction id")
        if not title:
            raise KickSchemaError("Missing prediction title")
        if not outcomes_raw:
            raise KickSchemaError("No outcomes in prediction")

        outcomes = []
        for o in outcomes_raw:
            outcomes.append(
                OutcomeInfo(
                    id=str(o.get("id", "")),
                    title=o.get("title", ""),
                    total_voters=int(o.get("total_voters") or 0),
                    total_points=int(o.get("total_points") or 0),
                    return_rate=float(o.get("return_rate") or 1.0),
                    is_winner=bool(o.get("is_winner") or o.get("winner")),
                )
            )

        # Resolved predictions often omit the top-level winning_outcome_id and
        # only flag the winner on the outcome itself.
        winning_outcome_id = (
            str(
                raw.get("winning_outcome_id") or raw.get("winningOutcomeId") or ""
            ).strip()
            or None
        )
        if not winning_outcome_id:
            for outcome in outcomes:
                if outcome.is_winner and outcome.id:
                    winning_outcome_id = outcome.id
                    break

        return PredictionEvent(
            kick_prediction_id=pred_id,
            channel_slug=channel_slug,
            title=title,
            outcomes=outcomes,
            state=state,
            winning_outcome_id=winning_outcome_id,
            opened_at=_utc(
                raw.get("created_at") or raw.get("opened_at") or datetime.now(tz=UTC)
            ),
            closes_at=_utc(raw["closes_at"]) if raw.get("closes_at") else None,
            resolved_at=_utc(raw["resolved_at"]) if raw.get("resolved_at") else None,
            raw=raw,
        )
    except KickSchemaError:
        raise
    except Exception as exc:
        raise KickSchemaError(f"Unexpected prediction shape: {exc}") from exc


class LegacyKickAdapter:
    """Implements KickGateway by delegating to legacy/src/client.py."""

    def __init__(self) -> None:
        if not _LEGACY_AVAILABLE:
            raise RuntimeError(
                "Legacy code is not importable — check sys.path / dependencies"
            )

    def _client(self, token: str) -> Any:
        return _KickClient(authorization=f"Bearer {token}")

    async def resolve_channel(self, slug: str, token: str) -> ChannelInfo:
        client = self._client(token)
        loop = asyncio.get_event_loop()
        data = await track_kick_call(
            "resolve_channel",
            asyncio.wait_for(
                loop.run_in_executor(None, client.get_channel, slug),
                timeout=get_settings().kick_timeout_seconds,
            ),
        )
        if not data or not isinstance(data, dict):
            raise KickSchemaError(f"resolve_channel({slug}): no data returned")
        channel_id = data.get("id")
        if not channel_id:
            raise KickSchemaError(f"resolve_channel({slug}): missing id")
        chatroom_id = (data.get("chatroom") or {}).get("id")
        try:
            return ChannelInfo(
                slug=slug,
                kick_channel_id=int(channel_id),
                chatroom_id=int(chatroom_id) if chatroom_id else None,
            )
        except (TypeError, ValueError) as exc:
            raise KickSchemaError(
                f"resolve_channel({slug}): invalid channel identifier"
            ) from exc

    async def get_active_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        client = self._client(token)
        loop = asyncio.get_event_loop()
        raw = await track_kick_call(
            "get_active_prediction",
            asyncio.wait_for(
                loop.run_in_executor(None, client.get_active_prediction, slug),
                timeout=get_settings().kick_timeout_seconds,
            ),
        )
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise KickSchemaError(
                f"get_active_prediction({slug}): expected dict, got {type(raw)}"
            )
        return _normalize_prediction(raw, slug)

    async def get_latest_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        """Return the channel's latest prediction in any state (or None).

        Unlike ``get_active_prediction`` this also surfaces locked/resolved
        predictions, which is how the poll fallback records winners and
        triggers settlement when the Pusher resolution event is missed.
        """
        client = self._client(token)
        loop = asyncio.get_event_loop()
        data = await track_kick_call(
            "get_latest_prediction",
            asyncio.wait_for(
                loop.run_in_executor(None, client.get_latest_prediction_data, slug),
                timeout=get_settings().kick_timeout_seconds,
            ),
        )
        if not data or not isinstance(data, dict):
            return None
        raw = data.get("prediction")
        if not isinstance(raw, dict):
            return None
        return _normalize_prediction(raw, slug)

    async def get_points_balance(self, slug: str, token: str) -> int | None:
        """Authenticated user's channel-points balance for ``slug``, or None.

        Uses GET https://kick.com/api/v2/channels/{slug}/points, which requires
        the kick.com site session token (same credential used to place votes).
        """
        client = self._client(token)
        loop = asyncio.get_event_loop()

        def _fetch():
            return client.get_channel_points(slug)

        try:
            data = await track_kick_call(
                "get_points_balance",
                asyncio.wait_for(
                    loop.run_in_executor(None, _fetch),
                    timeout=get_settings().kick_timeout_seconds,
                ),
            )
        except Exception as exc:
            log.warning(
                "get_points_balance_failed",
                slug=slug,
                error_type=type(exc).__name__,
            )
            return None
        if not isinstance(data, dict):
            return None
        points = (data.get("data") or {}).get("points")
        if points is None:
            return None
        try:
            return int(points)
        except (TypeError, ValueError):
            return None

    async def place_vote(
        self,
        slug: str,
        prediction_id: str,
        outcome_id: str,
        amount: int,
        token: str,
    ) -> VoteResult:
        client = self._client(token)
        loop = asyncio.get_event_loop()

        def _vote():
            return client.vote_prediction(slug, prediction_id, outcome_id, amount)

        try:
            ok = await track_kick_call(
                "place_vote",
                asyncio.wait_for(
                    loop.run_in_executor(None, _vote),
                    timeout=get_settings().kick_timeout_seconds,
                ),
            )
            if ok:
                return VoteResult(success=True, amount_placed=amount)
            return VoteResult(success=False, error="vote_api_error")
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            error = f"http_{status_code}" if status_code else type(exc).__name__
            return VoteResult(success=False, error=error)

    async def _listen_gen(self, slug: str) -> AsyncGenerator[PredictionEvent, None]:
        """Async generator wrapping the synchronous Pusher listener."""
        import queue

        q: queue.Queue[dict | None] = queue.Queue()

        def on_prediction(pred: dict) -> None:
            q.put(pred)

        def on_resolved(pred: dict) -> None:
            q.put(pred)

        # We need a channel_id; resolve it without a token for public channels
        # (Pusher subscription uses channel_id for predictions-channel-{id})
        # TODO(verify): confirm public vs auth requirements for predictions-channel-*
        try:
            # Use a throwaway client (no auth needed for channel lookup)
            client = _KickClient(authorization="")
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, client.get_channel, slug)
            channel_id = (data or {}).get("id")
            chatroom_id = ((data or {}).get("chatroom") or {}).get("id")
        except Exception as exc:
            log.warning(
                "listen_channel_lookup_failed",
                slug=slug,
                error_type=type(exc).__name__,
            )
            channel_id = None
            chatroom_id = None

        if not chatroom_id:
            log.warning("listen_no_chatroom", slug=slug)
            return

        pusher = _Pusher(
            username=slug,
            chatroom_id=int(chatroom_id),
            channel_id=int(channel_id) if channel_id else None,
            authorization="",
            on_prediction=on_prediction,
            on_prediction_resolved=on_resolved,
        )
        pusher.start()

        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    raw = await loop.run_in_executor(None, q.get, True, 60)
                except queue.Empty:
                    # No prediction events arrived within the queue timeout
                    # (idle channel). Keep listening instead of tearing down.
                    continue
                if raw is None:
                    break
                try:
                    event = _normalize_prediction(raw, slug)
                    yield event
                except KickSchemaError as exc:
                    log.warning("listen_schema_error", slug=slug, error=str(exc))
        finally:
            pusher.stop()

    def listen(self, slug: str) -> AsyncGenerator[PredictionEvent, None]:
        return self._listen_gen(slug)
