"""The hosted deployment: one container per tester, behind one proxy (deploy/).

Four contracts:

1. ACCESS_TOKEN locks the glasses socket (closed with 4401) and every HTTP
   route except /healthz; header ``X-Access-Token`` or ``?token=`` opens
   them. Unset, everything is open, exactly as on the Mac.
2. PERSONA_FILE seeds the persona once; DEMO_RESET_ON_START clears the same
   tables scripts/demo_reset.py does, and nothing measured.
3. Behind the /t/NAME prefix the routes still match, and a URL the server
   builds itself (a redirect) leads back through the prefix.
4. PORT is honoured, so the image's CMD can follow the platform.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pipeline.api.app import create_app
from pipeline.api.auth import token_matches
from pipeline.api.wiring import (
    DEMO_RESET_ALL_TABLES,
    DEMO_RESET_TABLES,
    build_pipeline,
    demo_reset,
    load_persona_file,
)
from pipeline.config import Settings
from pipeline.db import Database

from conftest import make_tick

TOKEN = "t0k-for-tests"


def sim_app(tmp_path: Path, **settings) -> tuple:
    pipeline = build_pipeline(Settings(db_path=tmp_path / "hosted.db", **settings),
                              source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    return pipeline, create_app(pipeline)


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


# -- 1. the token ---------------------------------------------------------------


def test_token_matches_is_open_when_unset_and_exact_when_set():
    assert token_matches("", None) and token_matches(None, "anything")
    assert token_matches("abc", "abc") and token_matches("abc", " abc ")
    assert not token_matches("abc", None)
    assert not token_matches("abc", "abd")
    assert not token_matches("abc", "")


async def test_api_routes_need_the_token_when_one_is_set(tmp_path):
    pipeline, app = sim_app(tmp_path, access_token=TOKEN)
    try:
        async with client_for(app) as client:
            assert (await client.get("/api/persona")).status_code == 401
            assert (await client.get("/api/persona",
                                     headers={"X-Access-Token": "wrong"})).status_code == 401
            assert (await client.get("/api/persona?token=wrong")).status_code == 401
            # Either way of presenting it works; the header is case-insensitive.
            assert (await client.get("/api/persona",
                                     headers={"X-Access-Token": TOKEN})).status_code == 200
            assert (await client.get("/api/persona",
                                     headers={"x-access-token": TOKEN})).status_code == 200
            assert (await client.get(f"/api/persona?token={TOKEN}")).status_code == 200
            # Writes too, not only reads.
            assert (await client.post("/api/speak", json={"text": "hi"})).status_code == 401
            # The frames route an <img> tag hits cannot set a header: ?token= opens it.
            assert (await client.get("/frames?refs=f_1")).status_code == 401
            assert (await client.get(f"/frames?refs=f_1&token={TOKEN}")).status_code == 410
    finally:
        pipeline.db.close()


async def test_only_minimal_healthz_stays_open_for_the_healthcheck(tmp_path):
    pipeline, app = sim_app(tmp_path, access_token=TOKEN)
    try:
        async with client_for(app) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert set(health.json()) == {"ok", "uptime_s"}
            assert health.json()["ok"] is True
            assert (await client.get("/api/status")).status_code == 401
            assert (await client.get("/api/status?token=" + TOKEN)).status_code == 200
    finally:
        pipeline.db.close()


async def test_no_token_configured_means_open_as_on_the_mac(tmp_path):
    pipeline, app = sim_app(tmp_path)
    try:
        async with client_for(app) as client:
            assert (await client.get("/api/persona")).status_code == 200
    finally:
        pipeline.db.close()


async def test_a_401_still_carries_cors_headers_and_preflight_passes(tmp_path):
    """A hosted dashboard must see "wrong token", not a CORS error that hides it."""

    pipeline, app = sim_app(tmp_path, access_token=TOKEN,
                            cors_origins="https://dash.example")
    origin = {"Origin": "https://dash.example"}
    try:
        async with client_for(app) as client:
            denied = await client.get("/api/persona", headers=origin)
            assert denied.status_code == 401
            assert denied.headers["access-control-allow-origin"] == "https://dash.example"
            preflight = await client.options("/api/persona", headers={
                **origin, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-access-token"})
            assert preflight.status_code == 200
    finally:
        pipeline.db.close()


def glasses_app(tmp_path: Path, token: str):
    """The real app with the capture routers mounted, never started: no Gemini,
    no ElevenLabs, only the socket handler under test."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "glasses.db", access_token=token,
                 elevenlabs_api_key=None, speech_mode="text"),
        source="glasses", reasoner_mode="fake", speed=1, seed_db=False, vlm="off")
    return pipeline, create_app(pipeline)


def test_glasses_socket_without_the_token_is_closed_with_4401(tmp_path):
    pipeline, app = glasses_app(tmp_path, TOKEN)
    try:
        client = TestClient(app)
        for url, headers in (("/ws/glasses", {}),
                             ("/ws/glasses?token=wrong", {}),
                             ("/ws/glasses", {"X-Access-Token": "wrong"})):
            with client.websocket_connect(url, headers=headers) as ws:
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_text()
            assert closed.value.code == 4401, url
        assert app.state.glasses_link.n_connects == 0, "a refused phone never registers"
    finally:
        pipeline.db.close()


def test_glasses_socket_with_the_token_streams(tmp_path):
    pipeline, app = glasses_app(tmp_path, TOKEN)
    try:
        client = TestClient(app)
        with client.websocket_connect(f"/ws/glasses?token={TOKEN}") as ws:
            ws.send_text('{"type": "hello", "caps": ["speak"]}')
        with client.websocket_connect("/ws/glasses", headers={"X-Access-Token": TOKEN}):
            pass
        assert app.state.glasses_link.n_connects == 2
    finally:
        pipeline.db.close()


def test_glasses_socket_is_open_with_no_token_configured(tmp_path):
    pipeline, app = glasses_app(tmp_path, "")
    try:
        with TestClient(app).websocket_connect("/ws/glasses"):
            pass
        assert app.state.glasses_link.n_connects == 1
    finally:
        pipeline.db.close()


# -- 2. persona and demo presets --------------------------------------------------


def test_persona_file_seeds_an_empty_database_once(tmp_path):
    persona = tmp_path / "persona.txt"
    persona.write_text("  You are Bryan, terse and kind.\n", encoding="utf-8")
    db = Database(tmp_path / "p.db").connect().init_schema()
    try:
        assert load_persona_file(db, persona) is True
        assert db.get_persona() == "You are Bryan, terse and kind."
        # A tester's own edit survives the next restart.
        db.set_persona("my edit")
        assert load_persona_file(db, persona) is False
        assert db.get_persona() == "my edit"
    finally:
        db.close()


def test_a_missing_or_empty_persona_file_is_not_fatal(tmp_path):
    db = Database(tmp_path / "p.db").connect().init_schema()
    try:
        assert load_persona_file(db, tmp_path / "nope.txt") is False
        (tmp_path / "blank.txt").write_text("\n")
        assert load_persona_file(db, tmp_path / "blank.txt") is False
        assert db.get_persona() is None
    finally:
        db.close()


def test_demo_reset_tables_match_the_script():
    """Two copies of one list (the script runs without the wiring); pin them equal."""

    script = Path(__file__).resolve().parents[1] / "scripts" / "demo_reset.py"
    spec = importlib.util.spec_from_file_location("demo_reset_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.TABLES) == DEMO_RESET_TABLES
    assert tuple(module.ALL_TABLES) == DEMO_RESET_ALL_TABLES


def test_build_pipeline_applies_both_presets_on_start(tmp_path):
    db_path = tmp_path / "presets.db"
    first = build_pipeline(Settings(db_path=db_path), source="sim",
                           reasoner_mode="fake", speed=1, seed_db=False)
    first.sessions.start("rehearsal")
    first.db.insert_tick(make_tick(seq=1))
    first.db.set_persona("kept")
    first.db.close()

    persona = tmp_path / "persona.txt"
    persona.write_text("from the file")
    second = build_pipeline(
        Settings(db_path=db_path, demo_reset_on_start="1", persona_file=str(persona)),
        source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    try:
        count = lambda table: second.db.conn.execute(  # noqa: E731
            f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        assert count("sessions") == 0, "short-term memory is cleared"
        assert count("ticks") == 1, "what was measured is kept"
        assert second.db.get_persona() == "kept", "an existing persona is not stamped over"
    finally:
        second.db.close()


def test_demo_reset_counts_what_it_cleared(tmp_path):
    pipeline = build_pipeline(Settings(db_path=tmp_path / "r.db"), source="sim",
                              reasoner_mode="fake", speed=1, seed_db=False)
    try:
        pipeline.sessions.start("a")
        assert demo_reset(pipeline.db) == 1
        assert demo_reset(pipeline.db) == 0
    finally:
        pipeline.db.close()


def test_demo_reset_all_clears_measured_day_and_only_live_biometrics(tmp_path):
    pipeline = build_pipeline(Settings(db_path=tmp_path / "all.db"), source="sim",
                              reasoner_mode="fake", speed=1, seed_db=False)
    try:
        db = pipeline.db
        db.insert_tick(make_tick(seq=1))
        with db._lock:
            db.conn.execute(
                "INSERT INTO biometric_series(t, metric, value, source, origin) "
                "VALUES (1, 'heart_rate', 70, 'seed', 'seed'), "
                "(2, 'heart_rate', 90, 'watch', 'live')"
            )
            db.conn.commit()
        assert demo_reset(db, all_data=True) == 2
        assert db.conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == 0
        rows = db.conn.execute(
            "SELECT origin FROM biometric_series ORDER BY t").fetchall()
        assert [row["origin"] for row in rows] == ["seed"]
    finally:
        pipeline.db.close()


def test_preset_env_vars_parse_leniently(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "abc")
    monkeypatch.setenv("DEMO_RESET_ON_START", "1")
    monkeypatch.setenv("PERSONA_FILE", "/data/persona.txt")
    s = Settings()
    assert (s.access_token, s.demo_reset_on_start, s.persona_file) == (
        "abc", True, Path("/data/persona.txt"))
    # Blank and mistyped values are "off", never "reset my memory".
    monkeypatch.setenv("DEMO_RESET_ON_START", "yse")
    monkeypatch.setenv("PERSONA_FILE", "")
    s = Settings()
    assert s.demo_reset_on_start is False and s.persona_file is None


@pytest.mark.parametrize("hosted_env", [
    {"ROOT_PATH": "/t/alice"},
    {"HOSTED": "1"},
])
def test_hosted_startup_refuses_an_empty_access_token(monkeypatch, hosted_env):
    monkeypatch.setenv("ACCESS_TOKEN", "  ")
    for key, value in hosted_env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="ACCESS_TOKEN must be non-empty"):
        Settings()


def test_local_startup_remains_open_without_a_token(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("HOSTED", "0")
    assert Settings().access_token == ""


def test_deploy_lifecycle_defaults_to_purge_and_supports_archive():
    deploy = Path(__file__).resolve().parents[2] / "deploy"
    remove = (deploy / "remove_tester.sh").read_text()
    new = (deploy / "new_tester.sh").read_text()
    tester = (deploy / "tester.yml").read_text()
    dockerfile = (deploy / "Dockerfile").read_text()
    patches = (deploy / "PATCHES.md").read_text()
    assert 'compose rm -sfv' in remove
    assert 'rm -rf -- "data/$name"' in remove
    assert '--archive' in remove and 'data/_removed' in remove
    assert "HOSTED=1" in new
    assert "/healthz" in new and "/api/status" not in new
    assert 'DEMO_RESET_ALL: "1"' in tester
    assert "urlopen('http://127.0.0.1:%s/healthz'" in dockerfile
    assert "asyncio.wait_for" in patches and "text_len=%d" in patches


# -- 3. behind the /t/NAME prefix ----------------------------------------------


async def test_routes_match_behind_a_stripping_proxy_and_redirects_keep_the_prefix(tmp_path):
    """Caddy strips /t/alice; uvicorn, given ROOT_PATH, puts it back into the
    scope's path and sets root_path. This transport builds that same scope."""

    pipeline, app = sim_app(tmp_path, access_token=TOKEN)
    transport = httpx.ASGITransport(app=app, root_path="/t/alice")
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Routes match, and only the minimal liveness route stays open.
            assert (await client.get("/t/alice/healthz")).status_code == 200
            assert (await client.get("/t/alice/api/status")).status_code == 401
            assert (await client.get("/t/alice/api/persona")).status_code == 401
            assert (await client.get(f"/t/alice/api/persona?token={TOKEN}")).status_code == 200
            # A URL the server builds itself leads back through the prefix.
            moved = await client.get(f"/t/alice/api/persona/?token={TOKEN}")
            assert moved.status_code == 307
            assert "/t/alice/api/persona" in moved.headers["location"]
    finally:
        pipeline.db.close()


def test_frame_urls_are_relative_to_the_backend_root():
    """The contract the dashboard joins onto its base URL (…/t/NAME): a path from
    the backend root, never a host or a hard-coded prefix."""

    from pipeline.recap import moments

    source = Path(moments.__file__).read_text()
    assert 'frame_url=f"/api/evidence/' in source


# -- 4. PORT ---------------------------------------------------------------------


def test_port_comes_from_the_environment(monkeypatch):
    from pipeline.main import build_parser, default_port

    monkeypatch.setenv("PORT", "8123")
    assert default_port() == 8123
    assert build_parser().parse_args([]).port == 8123
    assert build_parser().parse_args(["--port", "9000"]).port == 9000
    monkeypatch.setenv("PORT", "eighty")
    assert default_port() == 8010
    monkeypatch.delenv("PORT")
    assert default_port() == 8010


def test_tokens_in_urls_are_redacted_from_uvicorn_logs():
    import logging

    from pipeline.api.auth import RedactTokenFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/persona?token=s3cret&x=1", "1.1", 200), None)
    RedactTokenFilter().filter(record)
    line = record.getMessage()
    assert "s3cret" not in line and "token=***&x=1" in line

    ws = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                           '%s - "WebSocket %s" [accepted]',
                           ("1.2.3.4:5", "/ws/glasses?token=s3cret"), None)
    RedactTokenFilter().filter(ws)
    assert "s3cret" not in ws.getMessage()
