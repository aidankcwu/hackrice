"""A8 — GET /frames. Done when B can fetch four refs and get bytes back."""

from __future__ import annotations

import base64
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from longevity.ring import FrameRing
from longevity.server.frames import MAX_REFS, attach

JPEG = b"\xff\xd8\xff\xe0" + b"payload" * 100


def ref(i: int) -> str:
    return f"f_{i:08d}"


@pytest.fixture
def client() -> TestClient:
    """A ring holding four fresh frames and one that aged out 100 s ago."""
    ring = FrameRing()
    now = time.time()
    for i in range(1738, 1743):
        ring.put(ref(i), JPEG + bytes([i % 256]), t=now - (1742 - i), now=now)
    ring.put(ref(99), b"stale", t=now - 100, now=now)  # past the 90 s TTL

    app = FastAPI()
    attach(app, ring)
    c = TestClient(app)
    c.ring = ring  # type: ignore[attr-defined]
    return c


def test_b_can_fetch_four_refs_and_get_bytes_back(client: TestClient):
    """The §4.3 escalation call: four frames, base64, ready for the Claude request."""
    wanted = [ref(1738), ref(1740), ref(1741), ref(1742)]
    r = client.get("/frames", params={"refs": ",".join(wanted)})

    assert r.status_code == 200
    body = r.json()
    assert body["missing"] == []
    assert [f["ref"] for f in body["frames"]] == wanted
    for f in body["frames"]:
        assert f["mime"] == "image/jpeg"
        decoded = base64.b64decode(f["b64"])
        assert decoded.startswith(b"\xff\xd8\xff\xe0")
        assert f["bytes"] == len(decoded)


def test_returned_bytes_match_the_ring_exactly(client: TestClient):
    r = client.get("/frames", params={"refs": ref(1742)})
    stored = client.ring.get(ref(1742))  # type: ignore[attr-defined]
    assert base64.b64decode(r.json()["frames"][0]["b64"]) == stored.jpeg


def test_expired_ref_does_not_cost_b_the_good_frames(client: TestClient):
    """Partial success: three live refs plus one expired still returns the three."""
    r = client.get("/frames", params={"refs": f"{ref(1740)},{ref(99)},{ref(1742)}"})
    assert r.status_code == 200
    body = r.json()
    assert [f["ref"] for f in body["frames"]] == [ref(1740), ref(1742)]
    assert body["missing"] == [ref(99)]


def test_all_refs_expired_returns_410(client: TestClient):
    r = client.get("/frames", params={"refs": ref(99)})
    assert r.status_code == 410
    assert r.json()["missing"] == [ref(99)]


def test_unknown_ref_returns_410_not_a_crash(client: TestClient):
    r = client.get("/frames", params={"refs": "f_99999999"})
    assert r.status_code == 410
    assert r.json()["frames"] == []


def test_expired_ref_is_logged_rather_than_raised(client: TestClient, caplog):
    """§12.3: 'log it rather than crash, because it means the pipeline has fallen behind.'"""
    with caplog.at_level("WARNING"):
        client.get("/frames", params={"refs": f"{ref(1742)},{ref(99)}"})
    assert any(ref(99) in rec.getMessage() for rec in caplog.records)


def test_age_ms_is_reported_for_each_frame(client: TestClient):
    r = client.get("/frames", params={"refs": f"{ref(1738)},{ref(1742)}"})
    ages = [f["age_ms"] for f in r.json()["frames"]]
    assert ages[0] > ages[1]  # older frame first
    assert all(0 <= a <= 90_000 for a in ages)


def test_empty_refs_is_a_400(client: TestClient):
    r = client.get("/frames", params={"refs": "  , ,"})
    assert r.status_code == 400


def test_missing_refs_param_is_rejected(client: TestClient):
    assert client.get("/frames").status_code == 422


def test_refs_are_deduped_and_whitespace_tolerant(client: TestClient):
    r = client.get("/frames", params={"refs": f" {ref(1742)} , {ref(1742)},{ref(1741)} "})
    assert [f["ref"] for f in r.json()["frames"]] == [ref(1742), ref(1741)]


def test_ref_count_is_capped(client: TestClient):
    r = client.get("/frames", params={"refs": ",".join(f"f_{i:08d}" for i in range(100))})
    assert r.status_code == 410
    assert len(r.json()["missing"]) == MAX_REFS


def test_stats_endpoint_reports_ring_health(client: TestClient):
    body = client.get("/frames/stats").json()
    assert body["count"] == 5  # the 100 s-old frame is swept, not just hidden
    assert body["ttl_s"] == 90.0
    assert body["bytes"] > 0
    client.get("/frames", params={"refs": ref(1742)})
    assert client.get("/frames/stats").json()["hits"] >= 1
