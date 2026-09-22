"""API_TOKEN: one bearer gate on /api/*, the glasses socket, /frames and
/ingest/stats (docs/DEPLOY.md). The two OAuth callbacks are the only exemption.

Off when API_TOKEN is unset or blank, so a LAN demo and the rest of this suite
run exactly as before. The app is built on a real ``glasses`` pipeline so the
socket and frame routes under test are the ones ``create_app`` actually mounts;
the lifespan never runs (no ``with TestClient``), so no capture loop starts.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from longevity import wire
from longevity.server.ingest import INGEST_PATH
from pipeline.actions.speech import get_speak_fn, set_speak_fn
from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.wearables import fitbit_routes, google_health_routes

TOKEN = "s3cret-demo-token"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    speak_fn = get_speak_fn()  # a glasses build installs its own; hand it back after
    pipeline = build_pipeline(Settings(_env_file=None, db_path=tmp_path / "auth.db"),
                              source="glasses", reasoner_mode="fake", speed=1.0,
                              seed_db=False, vlm="fake")
    try:
        yield TestClient(create_app(pipeline))
    finally:
        set_speak_fn(speak_fn)
        pipeline.db.close()


@pytest.fixture
def token_on(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)


def ping_over_socket(client: TestClient, url: str, **kwargs) -> None:
    with client.websocket_connect(url, **kwargs) as ws:
        ws.send_text(json.dumps({"v": wire.PROTOCOL_VERSION, "type": wire.PING}))


# -- token set ------------------------------------------------------------------


def test_no_token_is_401_with_a_bearer_challenge(client, token_on):
    response = client.get("/api/status")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]


def test_bearer_header_is_accepted(client, token_on):
    assert client.get("/api/status", headers=BEARER).status_code == 200


def test_token_query_parameter_is_accepted(client, token_on):
    assert client.get(f"/api/status?token={TOKEN}").status_code == 200


@pytest.mark.parametrize("kwargs", [
    {"headers": {"Authorization": "Bearer wrong"}},
    {"params": {"token": "wrong"}},
    {"headers": {"Authorization": f"Basic {TOKEN}"}},
])
def test_wrong_token_is_401(client, token_on, kwargs):
    assert client.get("/api/status", **kwargs).status_code == 401


def test_every_api_route_is_behind_the_gate(client, token_on):
    # Evidence thumbnails are <img src> in the dashboard: 401 bare, served with ?token=.
    assert client.get("/api/evidence/d_none/f_none").status_code == 401
    assert client.get(f"/api/evidence/d_none/f_none?token={TOKEN}").status_code == 404
    assert client.post("/api/speak", json={"text": "hi"}).status_code == 401


def test_cors_preflight_is_not_challenged(client, token_on):
    """Browsers never send credentials on a preflight; CORS answers it first."""
    response = client.options("/api/status", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_glasses_socket_is_rejected_without_a_token(client, token_on):
    for url in (INGEST_PATH, f"{INGEST_PATH}?token=wrong"):
        with pytest.raises(WebSocketDisconnect) as closed:
            ping_over_socket(client, url)
        assert closed.value.code == 1008
    assert client.app.state.glasses_link.n_connects == 0


def test_glasses_socket_is_accepted_with_the_token(client, token_on):
    ping_over_socket(client, INGEST_PATH, headers=BEARER)
    ping_over_socket(client, f"{INGEST_PATH}?token={TOKEN}")
    link = client.app.state.glasses_link
    assert (link.n_connects, link.n_pings) == (2, 2)


# -- token unset: auth off --------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_token_turns_auth_off(client, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_TOKEN", value)
    assert client.get("/api/status").status_code == 200
    ping_over_socket(client, INGEST_PATH)
    assert client.app.state.glasses_link.n_connects == 1


# -- frames and ingest debug routes ---------------------------------------------------


@pytest.mark.parametrize("path", ["/frames?refs=f_00000001", "/frames/stats", "/ingest/stats"])
def test_frame_and_ingest_routes_are_behind_the_gate(client, token_on, path):
    """Frame refs are sequential, so an open /frames would hand out raw frames."""
    assert client.get(path).status_code == 401
    sep = "&" if "?" in path else "?"
    assert client.get(f"{path}{sep}token={TOKEN}").status_code != 401
    assert client.get(path, headers=BEARER).status_code != 401


def test_the_schema_stays_open_for_the_health_check(client, token_on):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


# -- OAuth callbacks: open, but only to a state /authorize issued -----------------------

PROVIDERS = [fitbit_routes, google_health_routes]


class FakeOAuthClient:
    def __init__(self) -> None:
        self.exchanged: list[tuple[str, str]] = []

    def authorize_url(self, state: str) -> tuple[str, str]:
        return f"https://provider.invalid/auth?state={state}", f"verifier-for-{state}"

    async def exchange_code(self, code: str, verifier: str) -> None:
        self.exchanged.append((code, verifier))


@pytest.fixture
def oauth(monkeypatch):
    """A configured sync per provider whose token exchange only records its call."""
    fakes = {}
    for module in PROVIDERS:
        fake = SimpleNamespace(client=FakeOAuthClient(), connected=False)
        monkeypatch.setattr(module, "_sync", fake)
        monkeypatch.setattr(module, "_pending", {})
        monkeypatch.setattr(module, "start_polling", lambda sync: None)
        fakes[module] = fake
    return fakes


@pytest.mark.parametrize("module", PROVIDERS)
def test_oauth_callback_without_a_token_rejects_a_forged_state(client, token_on, oauth, module):
    url = f"{module.router.prefix}/callback"
    response = client.get(url, params={"code": "stolen", "state": "forged"})
    assert response.status_code == 400
    assert oauth[module].client.exchanged == []
    assert oauth[module].connected is False


@pytest.mark.parametrize("module", PROVIDERS)
def test_oauth_authorize_needs_the_token_and_its_state_completes_the_callback(
        client, token_on, oauth, module):
    prefix = module.router.prefix
    assert client.get(f"{prefix}/authorize", follow_redirects=False).status_code == 401
    assert module._pending == {}

    started = client.get(f"{prefix}/authorize", headers=BEARER, follow_redirects=False)
    assert started.status_code == 307
    (state, verifier), = module._pending.items()

    # The provider's redirect back carries no token; the issued state is enough.
    done = client.get(f"{prefix}/callback", params={"code": "real", "state": state})
    assert done.status_code == 200
    assert oauth[module].client.exchanged == [("real", verifier)]
    # One use: a replayed state is rejected like a forged one.
    again = client.get(f"{prefix}/callback", params={"code": "real", "state": state})
    assert again.status_code == 400


@pytest.mark.parametrize("module", PROVIDERS)
def test_only_the_exact_callback_path_is_exempt(client, token_on, oauth, module):
    prefix = module.router.prefix
    assert client.get(f"{prefix}/status").status_code == 401
    assert client.get(f"{prefix}/callback/x", params={"code": "c", "state": "s"}).status_code == 401
