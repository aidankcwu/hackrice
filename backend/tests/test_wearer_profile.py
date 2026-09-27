"""The questionnaire paragraph sits under the persona; it never replaces it."""

from __future__ import annotations

import httpx

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.persona import WEARER_HEADING, effective_persona, join_persona


def test_a_second_questionnaire_replaces_the_first() -> None:
    db = Database(":memory:").connect().init_schema()
    try:
        db.set_persona("Be quiet.")
        db.set_wearer_profile("Uses he/him. Wants less sugar.")
        db.set_wearer_profile("Uses she/her. Wants more daylight.")
        got = effective_persona(db, "built-in")
        assert "he/him" not in got and got.count(WEARER_HEADING) == 1
        assert got == f"{WEARER_HEADING}\nUses she/her. Wants more daylight.\n\nBe quiet."
    finally:
        db.close()


def test_join_persona_cases() -> None:
    assert join_persona("Be quiet.", None) == "Be quiet."
    assert join_persona("Be quiet.", "  ") == "Be quiet."
    assert join_persona("Be quiet.", "Uses he/him.") == f"{WEARER_HEADING}\nUses he/him.\n\nBe quiet."
    assert join_persona("", "Uses he/him.") == f"{WEARER_HEADING}\nUses he/him."


def test_wearer_slot_is_separate_from_persona(tmp_path) -> None:
    db = Database(":memory:").connect().init_schema()
    try:
        assert db.get_wearer_profile() is None
        db.set_persona("Be quiet.")
        db.set_wearer_profile("Uses he/him.")
        assert db.get_persona() == "Be quiet."
        assert db.get_wearer_profile() == "Uses he/him."
        assert effective_persona(db, "built-in") == f"{WEARER_HEADING}\nUses he/him.\n\nBe quiet."
        db.set_persona("")
        assert effective_persona(db, "built-in") == f"{WEARER_HEADING}\nUses he/him.\n\nbuilt-in"
        db.set_wearer_profile("")
        assert db.get_wearer_profile() is None
        assert effective_persona(db, "built-in") == "built-in"
    finally:
        db.close()


async def test_wearer_route_round_trip(tmp_path) -> None:
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "routes.db"), source="sim",
        reasoner_mode="fake", speed=1, seed_db=False,
    )
    app = create_app(pipeline)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        before = (await client.get("/api/persona")).json()
        assert before["wearer"] == "" and before["effective"] == before["text"]

        got = (await client.put("/api/persona/wearer", json={"text": "Uses she/her."})).json()
        assert got["wearer"] == "Uses she/her."
        assert got["text"] == before["text"]                      # persona untouched
        assert got["effective"].startswith(f"{WEARER_HEADING}\nUses she/her.\n\n")
        assert pipeline.reasoner.current_persona() == got["effective"]

        (await client.put("/api/persona", json={"text": "Be blunt."})).json()
        got = (await client.get("/api/persona")).json()
        assert got["text"] == "Be blunt." and got["wearer"] == "Uses she/her."
        assert got["effective"] == f"{WEARER_HEADING}\nUses she/her.\n\nBe blunt."

        cleared = (await client.put("/api/persona/wearer", json={"text": ""})).json()
        assert cleared["wearer"] == "" and cleared["effective"] == "Be blunt."
        assert (await client.put("/api/persona/wearer", json={"text": 7})).status_code == 400
    pipeline.db.close()
