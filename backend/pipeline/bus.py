"""In-process tick fan-out.

SPEC §5.2: *drop, never queue*. A queued frame is a stale frame, and a growing
queue in a real-time system is a death spiral.

So each subscription is a **size-1 slot**, not a queue. Publishing overwrites
whatever the subscriber has not yet consumed and bumps a ``dropped`` counter.
:meth:`TickBus.publish` is synchronous and never awaits a subscriber, so a slow
consumer can never stall the tick producer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .models import Tick

log = logging.getLogger(__name__)

__all__ = ["Subscription", "TickBus"]


class Subscription:
    """A single consumer's size-1 mailbox.

    Iterate it with ``async for tick in sub``. If the consumer falls behind,
    intermediate ticks are dropped and ``sub.dropped`` counts them.
    """

    def __init__(self, name: str, bus: "TickBus | None" = None) -> None:
        self.name = name
        self.dropped = 0
        self.delivered = 0
        self._bus = bus
        self._slot: Tick | None = None
        self._event = asyncio.Event()
        self._closed = False

    # -- producer side (called from TickBus.publish, never awaits) -------

    def offer(self, tick: Tick) -> bool:
        """Put ``tick`` in the slot. Returns False if it displaced an unread one."""

        displaced = self._slot is not None
        if displaced:
            self.dropped += 1
        self._slot = tick
        self._event.set()
        return not displaced

    # -- consumer side ---------------------------------------------------

    async def get(self) -> Tick:
        """Await the next tick. Returns the freshest one, not the oldest."""

        while True:
            if self._slot is not None:
                tick = self._slot
                self._slot = None
                self._event.clear()
                self.delivered += 1
                return tick
            if self._closed:
                raise StopAsyncIteration
            await self._event.wait()

    @property
    def pending(self) -> int:
        """0 or 1 -- by construction there is never a backlog."""

        return 1 if self._slot is not None else 0

    def close(self) -> None:
        self._closed = True
        self._event.set()
        if self._bus is not None:
            self._bus.unsubscribe(self)

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self

    async def __anext__(self) -> Tick:
        if self._closed and self._slot is None:
            raise StopAsyncIteration
        return await self.get()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Subscription {self.name} delivered={self.delivered} "
            f"dropped={self.dropped}>"
        )


class TickBus:
    """Fan-out of the tick stream to any number of in-process consumers."""

    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self.published = 0

    def subscribe(self, name: str) -> Subscription:
        sub = Subscription(name, bus=self)
        self._subs.append(sub)
        log.debug("bus: subscriber %r attached", name)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def publish(self, tick: Tick) -> None:
        """Hand the tick to every subscriber. Never blocks, never awaits."""

        self.published += 1
        for sub in self._subs:
            if not sub.offer(tick):
                log.debug(
                    "bus: subscriber %r dropped a tick (total %d)", sub.name, sub.dropped
                )

    def close(self) -> None:
        for sub in list(self._subs):
            sub.close()

    @property
    def subscribers(self) -> list[Subscription]:
        return list(self._subs)

    @property
    def total_dropped(self) -> int:
        return sum(s.dropped for s in self._subs)
