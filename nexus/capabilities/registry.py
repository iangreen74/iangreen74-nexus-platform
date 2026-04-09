"""
Capability Registry.

Every write-side action NEXUS can take is registered here so the
operator has a single inventory of "things this system can do",
each annotated with blast radius, description, and rate limits.

The registry is also the choke point for execution: it enforces
rate limits and records outcomes, meaning no caller can bypass the
safety rails just by importing the action function directly.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from nexus.config import (
    BLAST_DANGEROUS,
    BLAST_MODERATE,
    BLAST_SAFE,
    MAX_HEALING_ACTIONS_PER_HOUR,
)

logger = logging.getLogger("nexus.capabilities")


class RateLimitExceeded(Exception):
    """Raised when a capability call would exceed the hourly healing limit."""


class UnknownCapability(KeyError):
    """Raised when execute() is called with an unregistered capability name."""


@dataclass
class Capability:
    name: str
    function: Callable[..., Any]
    blast_radius: str
    description: str
    requires_approval: bool = False


@dataclass
class ActionRecord:
    id: str
    name: str
    blast_radius: str
    kwargs: dict[str, Any]
    started_at: str
    ok: bool
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    def __init__(self, rate_limit_per_hour: int = MAX_HEALING_ACTIONS_PER_HOUR):
        self._caps: dict[str, Capability] = {}
        self._history: deque[ActionRecord] = deque(maxlen=500)
        self._call_times: deque[datetime] = deque()
        self._rate_limit = rate_limit_per_hour
        self._lock = threading.Lock()

    def register(self, capability: Capability) -> None:
        if capability.blast_radius not in (BLAST_SAFE, BLAST_MODERATE, BLAST_DANGEROUS):
            raise ValueError(f"Invalid blast_radius: {capability.blast_radius}")
        self._caps[capability.name] = capability
        logger.info(
            "Registered capability %s (%s)", capability.name, capability.blast_radius
        )

    def get(self, name: str) -> Capability:
        if name not in self._caps:
            raise UnknownCapability(name)
        return self._caps[name]

    def list_all(self) -> list[Capability]:
        return list(self._caps.values())

    def list_safe(self) -> list[Capability]:
        return [c for c in self._caps.values() if c.blast_radius == BLAST_SAFE]

    def recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        recs = list(self._history)[-limit:]
        return [rec.__dict__ for rec in reversed(recs)]

    # -- Rate limiting ------------------------------------------------------

    def _prune_calls(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        while self._call_times and self._call_times[0] < cutoff:
            self._call_times.popleft()

    def _check_rate(self) -> None:
        now = datetime.now(timezone.utc)
        self._prune_calls(now)
        if len(self._call_times) >= self._rate_limit:
            raise RateLimitExceeded(
                f"Healing rate limit hit: {self._rate_limit}/hour"
            )
        self._call_times.append(now)

    # -- Execution ----------------------------------------------------------

    def execute(self, name: str, **kwargs: Any) -> ActionRecord:
        cap = self.get(name)
        started = datetime.now(timezone.utc)
        record = ActionRecord(
            id=f"{name}-{int(started.timestamp() * 1000)}",
            name=name,
            blast_radius=cap.blast_radius,
            kwargs=kwargs,
            started_at=started.isoformat(),
            ok=False,
        )
        with self._lock:
            try:
                self._check_rate()
                result = cap.function(**kwargs)
                record.ok = True
                record.result = result
            except RateLimitExceeded as exc:
                record.error = str(exc)
                logger.warning("Rate limit blocked %s", name)
                raise
            except Exception as exc:  # noqa: BLE001 — we want to log and record
                record.error = f"{type(exc).__name__}: {exc}"
                logger.exception("Capability %s failed", name)
            finally:
                self._history.append(record)
        return record


# Shared singleton — capability modules import this to self-register.
registry = CapabilityRegistry()
