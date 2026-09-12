from __future__ import annotations

import asyncio
from types import SimpleNamespace

from longevity import wire
from longevity.server import ingest


def test_ping_is_counted_quietly(caplog) -> None:
    link = ingest.GlassesLink()
    with caplog.at_level("INFO"):
        ingest._handle(link, '{"v":1,"type":"ping"}')
    assert link.n_pings == 1
    assert "ignoring message" not in caplog.text


async def test_idle_socket_is_closed(monkeypatch) -> None:
    class Socket:
        def __init__(self):
            self.app = SimpleNamespace(state=SimpleNamespace())
            self.client = SimpleNamespace(host="test-phone")
            self.closed = None

        async def accept(self): pass
        async def receive(self): await asyncio.Event().wait()
        async def close(self, code): self.closed = code

    socket = Socket()
    monkeypatch.setattr(ingest, "INGEST_IDLE_TIMEOUT_S", 0.001)
    await ingest.glasses_ws(socket)
    link = socket.app.state.glasses_link
    assert socket.closed == 1001
    assert link.n_idle_closes == 1
    assert link.n_disconnects == 1
    assert not link.clients
