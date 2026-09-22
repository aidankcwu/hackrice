"""The wearer's protocol: items, today's status, done / undo, the CSV export.

Task 2.1. The adherence matcher that turns sightings into ``seen`` and closed
dose windows into ``missed`` is task 2.2; here only the table, the seed, and
the routes the phone and the dashboard read.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, timedelta
from typing import Iterator

import httpx
import pytest

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.db import Database, day_key

SEED_NAMES = {
    "Morning dose": ("dose", "07:00", "10:00"),
    "Evening dose": ("dose", "19:00", "22:00"),
    "Lunch window": ("meal", "11:30", "14:00"),
    "Wind‑down": ("winddown", "21:30", "23:00"),
    "Daylight walk": ("walk", "07:00", "16:00"),
}


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
async def client(tmp_path):
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "protocol.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    app = create_app(pipeline)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as http:
        yield http, pipeline
    pipeline.db.close()


def _new_item(**overrides) -> dict:
    body = {"name": "Metformin", "kind": "dose", "window_start": "08:00",
            "window_end": "09:30", "days": [0, 2, 4]}
    body.update(overrides)
    return body


# -- the table and the seed ------------------------------------------------


def test_first_run_seeds_the_five_default_items(db: Database) -> None:
    items = db.list_protocol_items()
    assert {i["name"]: (i["kind"], i["window_start"], i["window_end"])
            for i in items} == SEED_NAMES
    for item in items:
        assert item["days"] == [0, 1, 2, 3, 4, 5, 6]
        assert item["id"] and item["created_t"] > 0


def test_seed_runs_once_not_whenever_the_table_is_empty(tmp_path) -> None:
    path = tmp_path / "seed.db"
    first = Database(path).connect().init_schema()
    for item in first.list_protocol_items():
        assert first.delete_protocol_item(item["id"])
    first.close()

    again = Database(path).connect().init_schema()
    try:
        # The wearer emptied the list on purpose; a restart must not refill it.
        assert again.list_protocol_items() == []
        again.init_schema()
        assert again.list_protocol_items() == []
    finally:
        again.close()


def test_status_upsert_keeps_the_sighting_through_done_and_undo(db: Database) -> None:
    item = db.list_protocol_items()[0]
    db.set_protocol_status(item["id"], "2026-09-22", "seen", t=100.0,
                           seen_t=90.0, evidence_ref="d_0001/f_00000042")
    db.set_protocol_status(item["id"], "2026-09-22", "done", t=200.0)
    row = db.protocol_status(item["id"], "2026-09-22")
    assert row["status"] == "done"
    assert row["seen_t"] == 90.0
    assert row["evidence_ref"] == "d_0001/f_00000042"
    db.set_protocol_status(item["id"], "2026-09-22", "undone", t=300.0)
    assert db.protocol_status(item["id"], "2026-09-22")["status"] == "undone"
    with pytest.raises(ValueError):
        db.set_protocol_status(item["id"], "2026-09-22", "maybe", t=400.0)


# -- the routes ------------------------------------------------------------


async def test_create_and_list(client) -> None:
    http, _ = client

    listed = (await http.get("/api/protocol")).json()
    assert {row["name"] for row in listed} == set(SEED_NAMES)

    made = await http.post("/api/protocol", json=_new_item(name="  Metformin  "))
    assert made.status_code == 201
    item = made.json()
    assert set(item) == {"id", "name", "kind", "window_start", "window_end",
                         "days", "created_t"}
    assert item["name"] == "Metformin"
    assert item["days"] == [0, 2, 4]

    listed = (await http.get("/api/protocol")).json()
    assert len(listed) == 6
    assert item["id"] in {row["id"] for row in listed}

    # `days` defaults to every day.
    daily = (await http.post("/api/protocol", json={
        "name": "Creatine", "kind": "dose",
        "window_start": "07:00", "window_end": "08:00"})).json()
    assert daily["days"] == [0, 1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("bad", [
    {"name": ""},
    {"name": 7},
    {"kind": "supplement"},
    {"window_start": "7:00"},
    {"window_end": "24:00"},
    {"window_start": "10:00", "window_end": "09:00"},
    {"days": []},
    {"days": [7]},
    {"days": [True]},
    {"days": "0,1"},
])
async def test_create_rejects_a_bad_item(client, bad) -> None:
    http, _ = client
    assert (await http.post("/api/protocol", json=_new_item(**bad))).status_code == 400


async def test_update_and_delete(client) -> None:
    http, _ = client
    item = (await http.post("/api/protocol", json=_new_item())).json()

    put = await http.put(f"/api/protocol/{item['id']}",
                         json={"window_end": "10:00", "days": [1]})
    assert put.status_code == 200
    updated = put.json()
    assert updated["window_end"] == "10:00"
    assert updated["days"] == [1]
    assert updated["name"] == "Metformin"
    assert updated["created_t"] == item["created_t"]

    assert (await http.put(f"/api/protocol/{item['id']}",
                           json={"window_start": "11:00"})).status_code == 400
    assert (await http.put("/api/protocol/pi_nope",
                           json={"name": "x"})).status_code == 404

    gone = await http.delete(f"/api/protocol/{item['id']}")
    assert gone.json() == {"id": item["id"], "removed": True}
    assert item["id"] not in {r["id"] for r in (await http.get("/api/protocol")).json()}
    assert (await http.delete(f"/api/protocol/{item['id']}")).status_code == 404


async def test_today_lists_only_todays_items_with_their_status(client) -> None:
    http, pipeline = client
    today = (await http.get("/api/protocol/today")).json()
    weekday = date.fromisoformat(today["day"]).weekday()

    on = (await http.post("/api/protocol", json=_new_item(
        name="Today pill", days=[weekday]))).json()
    off = (await http.post("/api/protocol", json=_new_item(
        name="Other day pill", days=[(weekday + 1) % 7]))).json()

    today = (await http.get("/api/protocol/today")).json()
    ids = [row["id"] for row in today["items"]]
    assert on["id"] in ids and off["id"] not in ids
    assert len(ids) == 6  # five seeds (every day) + the one scheduled today
    row = next(r for r in today["items"] if r["id"] == on["id"])
    assert row["status"] == "waiting"
    assert row["seen_t"] is None and row["evidence_ref"] is None
    assert row["name"] == "Today pill"

    # A sighting stored by the matcher (task 2.2) shows up here.
    pipeline.db.set_protocol_status(on["id"], today["day"], "seen", t=1.0,
                                    seen_t=1.0, evidence_ref="d_0009/f_1")
    row = next(r for r in (await http.get("/api/protocol/today")).json()["items"]
               if r["id"] == on["id"])
    assert row["status"] == "seen" and row["evidence_ref"] == "d_0009/f_1"


async def test_done_and_undo(client) -> None:
    http, _ = client
    item = (await http.get("/api/protocol")).json()[0]

    done = await http.post(f"/api/protocol/{item['id']}/done")
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["id"] == item["id"]
    row = next(r for r in (await http.get("/api/protocol/today")).json()["items"]
               if r["id"] == item["id"])
    assert row["status"] == "done"

    undo = await http.post(f"/api/protocol/{item['id']}/undo")
    assert undo.status_code == 200 and undo.json()["status"] == "undone"
    row = next(r for r in (await http.get("/api/protocol/today")).json()["items"]
               if r["id"] == item["id"])
    assert row["status"] == "undone"

    assert (await http.post("/api/protocol/pi_nope/done")).status_code == 404
    assert (await http.post("/api/protocol/pi_nope/undo")).status_code == 404


async def test_export_csv(client) -> None:
    http, pipeline = client
    db = pipeline.db
    today = (await http.get("/api/protocol/today")).json()["day"]
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    long_ago = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    morning = next(i for i in db.list_protocol_items() if i["name"] == "Morning dose")
    db.set_protocol_status(morning["id"], yesterday, "missed", t=1.0)
    db.set_protocol_status(morning["id"], long_ago, "done", t=1.0)
    await http.post(f"/api/protocol/{morning['id']}/done")

    res = await http.get("/api/protocol/export.csv?days=14")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert list(rows[0]) == ["day", "item_id", "name", "kind", "window_start",
                             "window_end", "status", "seen_t", "evidence_ref",
                             "updated_t"]
    mine = {r["day"]: r["status"] for r in rows if r["item_id"] == morning["id"]}
    assert mine[today] == "done"
    assert mine[yesterday] == "missed"
    assert long_ago not in mine  # outside the 14-day window
    assert all(r["day"] >= (date.fromisoformat(today)
                            - timedelta(days=13)).isoformat() for r in rows)
    # Every seeded item has a row today, waiting unless something was recorded.
    todays = {r["name"]: r["status"] for r in rows if r["day"] == today}
    assert set(todays) == set(SEED_NAMES)
    assert todays["Evening dose"] == "waiting"
    assert [r["day"] for r in rows] == sorted(r["day"] for r in rows)

    assert (await http.get("/api/protocol/export.csv?days=0")).status_code == 422


async def test_today_uses_the_pipeline_clock(client) -> None:
    http, pipeline = client
    body = (await http.get("/api/protocol/today")).json()
    assert body["day"] == day_key(pipeline.last_tick.t if pipeline.last_tick
                                  else time.time())
