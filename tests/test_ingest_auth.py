"""The glasses socket's auth hook (hosted backend, deploy/README.md).

The hosting app puts the expected token on ``app.state.access_token``; absent or
empty, the socket is open, as it always was on the Mac on the venue Wi-Fi.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from longevity.server import ingest


def app_with(token: str | None) -> FastAPI:
    app = FastAPI()
    app.include_router(ingest.router)
    if token is not None:
        app.state.access_token = token
    return app


def test_open_when_the_host_sets_no_token() -> None:
    for app in (app_with(None), app_with("")):
        with TestClient(app).websocket_connect(ingest.INGEST_PATH):
            pass
        assert ingest.link_of(app).n_connects == 1


def test_wrong_or_missing_token_is_closed_with_4401() -> None:
    app = app_with("s3cret")
    client = TestClient(app)
    for url, headers in ((ingest.INGEST_PATH, {}),
                         (ingest.INGEST_PATH + "?token=nope", {}),
                         (ingest.INGEST_PATH, {"X-Access-Token": "nope"})):
        with client.websocket_connect(url, headers=headers) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
        assert closed.value.code == ingest.CLOSE_UNAUTHORIZED == 4401
    assert ingest.link_of(app).n_connects == 0


def test_query_param_or_header_is_accepted() -> None:
    app = app_with("s3cret")
    client = TestClient(app)
    with client.websocket_connect(ingest.INGEST_PATH + "?token=s3cret"):
        pass
    with client.websocket_connect(ingest.INGEST_PATH, headers={"X-Access-Token": "s3cret"}):
        pass
    assert ingest.link_of(app).n_connects == 2


def test_authorization_bearer_is_accepted() -> None:
    app = app_with("s3cret")
    client = TestClient(app)
    for value in ("Bearer s3cret", "bearer s3cret"):
        with client.websocket_connect(ingest.INGEST_PATH, headers={"Authorization": value}):
            pass
    assert ingest.link_of(app).n_connects == 2


@pytest.mark.parametrize("url_suffix,headers", [
    ("", {"Authorization": "Bearer nope"}),
    ("", {"Authorization": "Basic s3cret"}),
    # X-Access-Token outranks Bearer: a wrong one is not rescued by a right Bearer.
    ("", {"X-Access-Token": "nope", "Authorization": "Bearer s3cret"}),
    # Bearer outranks ?token=: same rule one step down.
    ("?token=s3cret", {"Authorization": "Bearer nope"}),
])
def test_bearer_precedence_refuses_with_4401(url_suffix, headers) -> None:
    app = app_with("s3cret")
    with TestClient(app).websocket_connect(ingest.INGEST_PATH + url_suffix,
                                           headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4401
    assert ingest.link_of(app).n_connects == 0


@pytest.mark.parametrize("url_suffix,headers", [
    ("", {"X-Access-Token": "s3cret", "Authorization": "Bearer nope"}),
    ("?token=nope", {"Authorization": "Bearer s3cret"}),
    ("?token=s3cret", {"Authorization": "Basic nope"}),
    ("", {"X-Access-Token": "", "Authorization": "Bearer s3cret"}),
])
def test_bearer_precedence_accepts(url_suffix, headers) -> None:
    app = app_with("s3cret")
    with TestClient(app).websocket_connect(ingest.INGEST_PATH + url_suffix, headers=headers):
        pass
    assert ingest.link_of(app).n_connects == 1


# -- hosted hardening routed from deploy/PATCHES.md -----------------------

class _NeverSends:
    """A phone socket whose send never completes: a stalled cellular link."""
    async def send_text(self, message):  # noqa: D401
        import asyncio
        await asyncio.sleep(3600)


async def test_a_stalled_phone_send_is_dropped_within_the_deadline(monkeypatch):
    import asyncio, time
    from longevity.server import ingest
    monkeypatch.setattr(ingest, "PHONE_SEND_TIMEOUT_S", 0.2)
    link = ingest.GlassesLink()
    ws = _NeverSends()
    link.add_client(ws)
    t0 = time.monotonic()
    ok = await link.send_to(ws, '{"v":1,"type":"ping"}')
    assert ok is False and time.monotonic() - t0 < 1.0
    assert ws not in link.clients, "a stalled socket is dropped, not kept"


async def test_hosted_answer_log_records_length_not_text(monkeypatch, caplog):
    import logging
    from longevity.server import ingest
    monkeypatch.setenv("HOSTED", "1")
    link = ingest.GlassesLink()
    seen = []
    link.on_answer = lambda qid, text, heard, t: seen.append(text)
    with caplog.at_level(logging.INFO, logger="longevity.server.ingest"):
        ingest._handle(link, '{"v":1,"type":"answer","question_id":"q_1","text":"just the water","heard":true,"t":1.0}')
    assert seen == ["just the water"]
    line = next(r.getMessage() for r in caplog.records if "answer to q_1" in r.getMessage())
    assert "text_len=14" in line and "just the water" not in line
