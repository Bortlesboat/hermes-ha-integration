"""In-memory, HA-origin request lifecycle; no conversation or backend data."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
EVENT_CONVERSATION_ACTIVITY = "hermes_conversation_activity"
CONVERSATION_RESULTS = ("completed", "partial", "failed", "cancelled")
ACTIVITY_ERROR_CATEGORIES = frozenset(
    {"auth", "timeout", "redirect", "unsupported", "malformed", "server", "unreachable"}
)


@dataclass(frozen=True, slots=True)
class ConversationRequestToken:
    """Only locally generated identifiers and timing, never a backend session."""

    request_id: str
    started_at: datetime
    started_monotonic: float


class ConversationActivityTracker:
    """Per-entry synchronous observer for local conversation requests."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self.entry_id = entry_id
        self._pending: dict[str, ConversationRequestToken] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._closed = False
        self.last_request_duration_seconds: float | None = None
        self.last_request_result: str | None = None

    @property
    def active_request_count(self) -> int:
        return len(self._pending)

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to changes; entity lifecycle owns the returned remover."""
        if self._closed:
            return lambda: None
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self, payload: dict) -> None:
        try:
            self._hass.bus.async_fire(EVENT_CONVERSATION_ACTIVITY, payload)
        except Exception:
            _LOGGER.warning(
                "Could not publish Hermes request activity event", exc_info=True
            )
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.warning(
                    "Could not update Hermes request activity observer", exc_info=True
                )

    def start(self) -> ConversationRequestToken | None:
        if self._closed:
            return None
        token = ConversationRequestToken(
            str(uuid.uuid4()), datetime.now(timezone.utc), time.monotonic()
        )
        self._pending[token.request_id] = token
        self._notify(
            {
                "entry_id": self.entry_id,
                "request_id": token.request_id,
                "source": "home_assistant",
                "status": "started",
                "active_requests": self.active_request_count,
            }
        )
        return token

    def finish(
        self,
        token: ConversationRequestToken | None,
        outcome: str,
        *,
        error_category: str | None = None,
    ) -> bool:
        """Finish once, regardless of bus/observer failures or unload."""
        if (
            self._closed
            or token is None
            or self._pending.get(token.request_id) is not token
        ):
            return False
        if outcome not in CONVERSATION_RESULTS:
            raise ValueError("Invalid request outcome")
        del self._pending[token.request_id]
        duration = max(0.0, time.monotonic() - token.started_monotonic)
        self.last_request_duration_seconds = duration
        self.last_request_result = outcome
        payload = {
            "entry_id": self.entry_id,
            "request_id": token.request_id,
            "source": "home_assistant",
            "status": outcome,
            "active_requests": self.active_request_count,
            "duration_seconds": duration,
        }
        if (
            isinstance(error_category, str)
            and error_category in ACTIVITY_ERROR_CATEGORIES
        ):
            payload["error_category"] = error_category
        self._notify(payload)
        return True

    def shutdown(self) -> None:
        """Stop stale observers and locally cancel pending requests on unload."""
        if self._closed:
            return
        for token in tuple(self._pending.values()):
            self.finish(token, "cancelled")
        self._closed = True
        self._listeners.clear()
