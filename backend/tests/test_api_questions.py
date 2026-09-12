"""The HTTP face of ask/answer, and the transport underneath it (§8.2, §8.3, §8.11).

Two halves that fail in opposite directions:

* the **routes** are the demo's hands — ``/api/ask`` and ``/api/answer`` are how
  the loop gets driven with no phone in the room, and ``reported`` on
  ``/api/episodes`` is the only place an answer ever reaches the dashboard,
  because episodes are recomputed every tick and cannot carry it (§8.3);
* ``send_question`` is the **order** the whole feature rests on — audio first,
  then the instruction to listen. Reversed, the phone opens its microphone while
  the glasses are still reading the question out and transcribes them.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from types import SimpleNamespace

from longevity import wire
from pipeline.api.routes import router
from pipeline.capture import speak
from pipeline.capture.bridge import LongevityCapture
from pipeline.config import Settings
from pipeline.db import Database, day_key
from pipeline.models import Episode, PendingQuestion
from pipeline.reasoner.schema import AskAction

from fastapi import FastAPI

T0 = 1_757_700_000.0
DAY = day_key(T0)


# -- routes ---------------------------------------------------------------


class StubManager:
    """Stands in for S2's ``QuestionManager`` across the §8.11 interface only."""

    def __init__(self, *, row: PendingQuestion | None = None, reason: str | None = None):
        self._row = row
        self._reason = reason
        self.asked: list[tuple] = []
        self.answered: list[tuple] = []

    def ask(self, *, decision_id, t, episode_id, action, followup_of=None):
        self.asked.append((decision_id, t, episode_id, action))
        return self._row, self._reason

    def on_answer(self, question_id, text, heard, t):
        self.answered.append((question_id, text, heard, t))


def make_question(qid="q_00000001", **kwargs) -> PendingQuestion:
    fields = {
        "id": qid,
        "created_t": T0,
        "question": "Is that yours?",
        "answer_kind": "yes_no",
        "fills": "confirmed",
    }
    fields.update(kwargs)
    return PendingQuestion(**fields)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "questions.db").connect().init_schema()
    yield database
    database.close()


def client_for(db, manager=None) -> httpx.AsyncClient:
    """A bare app over one DB. No pipeline machinery: these routes are thin."""
    app = FastAPI()
    app.include_router(router)
    pipeline = SimpleNamespace(
        db=db,
        last_tick=SimpleNamespace(t=T0),
        clock=SimpleNamespace(speed=1.0, wall_to_tick=lambda t: t),
    )
    if manager is not None:
        pipeline.questions = manager
    app.state.pipeline = pipeline
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_questions_route_lists_newest_first(db):
    db.insert_question(make_question("q_1", created_t=T0))
    db.insert_question(make_question("q_2", created_t=T0 + 5, status="answered",
                                     answer_text="yes"))

    async with client_for(db) as http:
        rows = (await http.get("/api/questions")).json()

    assert [r["id"] for r in rows] == ["q_2", "q_1"]
    assert rows[0]["answer_text"] == "yes"
    assert {"parsed", "fills", "answer_kind", "status"} <= rows[0].keys()


async def test_questions_route_honours_the_limit(db):
    for i in range(5):
        db.insert_question(make_question(f"q_{i}", created_t=T0 + i))

    async with client_for(db) as http:
        assert len((await http.get("/api/questions?limit=2")).json()) == 2


async def test_questions_route_works_without_a_manager(db):
    """Reading the rows is a DB read; it must not need the manager to be wired."""
    async with client_for(db) as http:
        assert (await http.get("/api/questions")).status_code == 200


async def test_answer_without_an_id_goes_to_the_open_question(db):
    db.insert_question(make_question("q_open", expires_t=T0 + 25, sent_t=T0))
    manager = StubManager()

    async with client_for(db, manager) as http:
        body = (await http.post("/api/answer", json={"text": "yeah, two"})).json()

    assert body == {"question_id": "q_open", "accepted": True}
    assert manager.answered == [("q_open", "yeah, two", True, T0)]


async def test_answer_is_stamped_on_the_tick_clock(db):
    """`--speed 10` must not hand the manager a wall-clock timestamp (SPEC §14.2)."""
    db.insert_question(make_question("q_open"))
    manager = StubManager()

    async with client_for(db, manager) as http:
        await http.post("/api/answer", json={"text": "yes"})

    assert manager.answered[0][3] == T0


async def test_answer_with_no_open_question_is_a_404(db):
    db.insert_question(make_question("q_done", status="answered"))
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post("/api/answer", json={"text": "yes"})

    assert response.status_code == 404
    assert manager.answered == []


async def test_answer_can_name_a_question_explicitly(db):
    manager = StubManager()

    async with client_for(db, manager) as http:
        body = (await http.post(
            "/api/answer", json={"question_id": "q_x", "text": "", "heard": False}
        )).json()

    assert body["question_id"] == "q_x"
    assert manager.answered == [("q_x", "", False, T0)]


async def test_answer_without_a_manager_is_a_503(db):
    db.insert_question(make_question("q_open"))

    async with client_for(db) as http:
        response = await http.post("/api/answer", json={"text": "yes"})

    assert response.status_code == 503
    assert response.json() == {"error": "questions unavailable"}


async def test_ask_route_goes_through_the_guards(db):
    """Unlike /api/speak, a manual ask is still subject to §4 -- see the route."""
    manager = StubManager(row=make_question("q_new"))

    async with client_for(db, manager) as http:
        body = (await http.post("/api/ask", json={
            "text": "How many?", "answer_kind": "count", "fills": "count",
            "episode_id": "e_0003",
        })).json()

    assert body == {"question_id": "q_new", "suppressed_reason": None}
    decision_id, t, episode_id, action = manager.asked[0]
    assert (decision_id, t, episode_id) == ("manual", T0, "e_0003")
    assert isinstance(action, AskAction)
    assert (action.text, action.answer_kind, action.fills) == ("How many?", "count", "count")


async def test_a_suppressed_ask_says_which_guard_said_no(db):
    manager = StubManager(row=None, reason="one_open")

    async with client_for(db, manager) as http:
        body = (await http.post("/api/ask", json={"text": "Is that yours?"})).json()

    assert body == {"question_id": None, "suppressed_reason": "one_open"}


async def test_ask_needs_text(db):
    manager = StubManager()

    async with client_for(db, manager) as http:
        assert (await http.post("/api/ask", json={"text": "  "})).status_code == 400
    assert manager.asked == []


async def test_ask_with_an_invalid_answer_kind_is_a_400_not_a_500(db):
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post(
            "/api/ask", json={"text": "How many?", "answer_kind": "interpretive dance"}
        )

    assert response.status_code == 400
    assert manager.asked == []


async def test_ask_without_a_manager_is_a_503(db):
    async with client_for(db) as http:
        response = await http.post("/api/ask", json={"text": "Is that yours?"})

    assert response.status_code == 503


# -- the `reported` projection (§8.3) -------------------------------------


def _episode(db, episode_id="e_0001") -> Episode:
    episode = Episode(id=episode_id, kind="alcohol_sighting", start_t=T0,
                      end_t=T0 + 30, duration_s=30.0,
                      dominant={"alcohol_visible": True}, tick_count=20, open=False)
    db.upsert_episode(episode)
    return episode


async def test_an_episode_with_no_question_reports_nothing(db):
    _episode(db)

    async with client_for(db) as http:
        rows = (await http.get(f"/api/episodes?day={DAY}")).json()

    assert rows[0]["reported"] is None
    assert {"id", "kind", "dominant", "tick_count", "open"} <= rows[0].keys(), \
        "the projection is additive; nothing existing may be dropped"


async def test_an_answered_question_is_projected_onto_its_episode(db):
    _episode(db)
    db.insert_question(make_question(
        "q_root", episode_id="e_0001", status="answered", answer_text="yeah, two",
        answer_t=T0 + 12,
        parsed={"understood": True, "confirmed": True, "count": 2.0,
                "food_type": None, "note": "having two beers"},
    ))

    async with client_for(db) as http:
        reported = (await http.get(f"/api/episodes?day={DAY}")).json()[0]["reported"]

    assert reported == {
        "confirmed": True, "count": 2.0, "food_type": None,
        "note": "having two beers", "question_id": "q_root", "answered_t": T0 + 12,
    }


async def test_the_newest_parsed_answer_wins(db):
    """The follow-up ("how many?") lands after its root and is the better answer."""
    _episode(db)
    db.insert_question(make_question(
        "q_root", created_t=T0, episode_id="e_0001", status="answered",
        answer_t=T0 + 5, parsed={"confirmed": True, "note": "yes, mine"},
    ))
    db.insert_question(make_question(
        "q_followup", created_t=T0 + 10, episode_id="e_0001", status="answered",
        answer_t=T0 + 15, followup_of="q_root",
        parsed={"confirmed": True, "count": 2.0, "note": "two of them"},
    ))

    async with client_for(db) as http:
        reported = (await http.get(f"/api/episodes?day={DAY}")).json()[0]["reported"]

    assert reported["question_id"] == "q_followup"
    assert reported["count"] == 2.0


async def test_expired_and_unparsed_rows_report_nothing(db):
    """Silence is never a yes (§1), and a parse that never ran has nothing to say."""
    _episode(db)
    db.insert_question(make_question("q_gone", created_t=T0, episode_id="e_0001",
                                     status="expired"))
    db.insert_question(make_question("q_busy", created_t=T0 + 2, episode_id="e_0001",
                                     status="answered", answer_text="two", parsed={}))

    async with client_for(db) as http:
        assert (await http.get(f"/api/episodes?day={DAY}")).json()[0]["reported"] is None


# -- send_question: audio first, then the mic (§8.2) ----------------------


class RecordingLink:
    """A phone that records what it was told, in the order it was told.

    Sockets are real objects here, not a count, because the ask path picks one
    and sends both halves of the exchange to *that* one -- which is only
    testable if the stub can tell its sockets apart.
    """

    def __init__(self, phones: int = 1, caps: tuple[str, ...] = ("ask",)) -> None:
        self.phones = phones
        self.clients = {f"ws{i}" for i in range(phones)}
        self.messages: list[dict] = []
        self.broadcasts: list[dict] = []
        self.sent_to: list[object] = []
        self._caps = set(caps)
        self.fail_on: str | None = None

    async def send_text(self, message: str) -> int:
        if self.phones:
            decoded = wire.decode(message)
            self.messages.append(decoded)
            self.broadcasts.append(decoded)
        return self.phones

    async def send_to(self, ws: object, message: str) -> bool:
        decoded = wire.decode(message)
        if self.fail_on is not None and decoded.get("type") == self.fail_on:
            self.clients.discard(ws)
            return False
        self.messages.append(decoded)
        self.sent_to.append(ws)
        return True

    def pick(self, cap: str | None = None):
        if cap is not None and cap not in self._caps:
            return None
        return next(iter(sorted(self.clients)), None)

    def supports(self, cap: str) -> bool:
        return self.pick(cap) is not None

    @property
    def types(self) -> list[str]:
        return [m.get("type") for m in self.messages]


def make_capture(tmp_path, link, **overrides) -> LongevityCapture:
    settings = Settings(db_path=tmp_path / "unused.db", speech_mode="text", **overrides)
    capture = LongevityCapture(
        settings, source="replay", our_bus=SimpleNamespace(publish=lambda _t: None),
        dir=str(tmp_path), speed=1, loop=False, camera=0, vlm="off", flow=None,
    )
    capture.link = link
    return capture


async def test_the_audio_goes_out_before_the_mic_opens(tmp_path):
    link = RecordingLink()
    capture = make_capture(tmp_path, link)
    question = make_question("q_abc", question="Is that yours?", answer_kind="yes_no")

    assert await capture.send_question(question) is True
    assert link.types == ["speak", "ask"], "reversed, the phone transcribes the glasses"
    ask = link.messages[1]
    assert ask["question_id"] == "q_abc"
    assert ask["text"] == "Is that yours?"
    assert ask["answer_kind"] == "yes_no"


async def test_the_listen_window_comes_from_the_timings(tmp_path):
    link = RecordingLink()
    capture = make_capture(tmp_path, link, demo_mode=True)

    await capture.send_question(make_question())

    assert link.messages[1]["listen_s"] == capture.settings.timings.ask_listen_s


async def test_no_phone_means_no_ask_and_a_false(tmp_path):
    """A question nobody heard must not leave a row waiting for an answer (§8.2)."""
    link = RecordingLink(phones=0)
    capture = make_capture(tmp_path, link)

    assert await capture.send_question(make_question()) is False
    assert link.messages == []


async def test_supports_ask_reads_the_phones_hello(tmp_path):
    assert make_capture(tmp_path, RecordingLink(caps=("ask",))).supports_ask() is True
    assert make_capture(tmp_path, RecordingLink(caps=())).supports_ask() is False


async def test_a_spoken_question_is_counted_as_speech(tmp_path):
    """One synthesiser per link, so /api/status does not under-report utterances."""
    capture = make_capture(tmp_path, RecordingLink())

    await capture.send_question(make_question())

    assert capture.speech_stats()["sent"] == 1


async def test_both_halves_of_the_exchange_go_to_one_socket(tmp_path):
    """The mic must open on the phone that played the question, not on a peer.

    Broadcasting both messages looks identical with one phone connected and
    silently splits the exchange the moment a second socket exists -- a phone
    reconnecting mid-question is exactly when it does.
    """
    link = RecordingLink(phones=2)
    capture = make_capture(tmp_path, link)

    assert await capture.send_question(make_question("q_pin")) is True
    assert link.types == ["speak", "ask"]
    assert len(set(link.sent_to)) == 1, "audio and ask landed on different phones"
    assert link.broadcasts == [], "the exchange was broadcast, not pinned"


async def test_a_phone_that_cannot_listen_is_never_spoken_to(tmp_path):
    """The pick happens first: no `ask` capability, no audio either.

    Speaking the question and only then discovering nobody can answer it reads
    the wearer a question into a room with no microphone (§8.7).
    """
    link = RecordingLink(caps=())
    capture = make_capture(tmp_path, link)

    assert await capture.send_question(make_question()) is False
    assert link.messages == []


async def test_audio_that_fails_on_the_pinned_socket_never_opens_the_mic(tmp_path):
    link = RecordingLink()
    link.fail_on = "speak"
    capture = make_capture(tmp_path, link)

    assert await capture.send_question(make_question()) is False
    assert link.types == []


async def test_an_ask_that_fails_after_the_audio_is_still_a_false(tmp_path):
    """§8.2: the row must not sit open waiting on a mic that never opened."""
    link = RecordingLink()
    link.fail_on = "ask"
    capture = make_capture(tmp_path, link)

    assert await capture.send_question(make_question()) is False
    assert link.types == ["speak"]


async def test_synthesis_past_its_deadline_falls_back_to_the_phones_voice(
    tmp_path, monkeypatch
):
    """A hung ElevenLabs call is a TTS failure, not a question that never ships.

    Without the deadline the send coroutine waits on an HTTP call that may never
    return, and the row stays `open` forever -- `expires_t` is only set after a
    successful send, so nothing ever expires it (§8.2, §8.4).
    """
    monkeypatch.setattr(speak, "SYNTH_TIMEOUT_S", 0.01)
    link = RecordingLink()
    capture = make_capture(tmp_path, link)

    class HangingTTS:
        async def synthesize(self, text: str) -> bytes:
            await asyncio.sleep(30)
            return b"too late"

    speech = speak.make_speech(link, Settings(speech_mode="text"))
    speech.tts = HangingTTS()

    assert await capture.send_question(make_question()) is True
    assert link.types == ["speak", "ask"], "the phone's own voice carried it"
    assert speech.stats.tts_failures == 1
    assert "TimeoutError" in (speech.stats.last_error or "")
    assert speech.stats.sent == 1, "a fallback utterance still counts as speech"


# -- request validation (§8.11) -------------------------------------------


@pytest.mark.parametrize("question_id", [7, "", None, ["q_x"]])
async def test_a_wrong_typed_question_id_is_a_400_not_the_open_question(db, question_id):
    """Answering *something else* is the one failure nobody sees afterwards."""
    db.insert_question(make_question("q_open", expires_t=T0 + 25, sent_t=T0))
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post(
            "/api/answer", json={"question_id": question_id, "text": "yes"}
        )

    assert response.status_code == 400
    assert manager.answered == [], "the open question was answered by mistake"


@pytest.mark.parametrize("text", [None, 7, {"text": "yes"}, ["yes"]])
async def test_a_non_string_answer_text_is_a_400(db, text):
    """`str(None)` is the string "None" -- a transcript the wearer never said."""
    db.insert_question(make_question("q_open"))
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post("/api/answer", json={"text": text})

    assert response.status_code == 400
    assert manager.answered == []


@pytest.mark.parametrize("heard", [None, "false", 0, 1])
async def test_a_non_boolean_heard_is_a_400(db, heard):
    """`bool("false")` is True; coercion here would turn "the mic failed" into an answer."""
    db.insert_question(make_question("q_open"))
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post(
            "/api/answer", json={"text": "", "heard": heard}
        )

    assert response.status_code == 400
    assert manager.answered == []


async def test_a_missing_heard_still_defaults_to_true(db):
    db.insert_question(make_question("q_open"))
    manager = StubManager()

    async with client_for(db, manager) as http:
        await http.post("/api/answer", json={"text": "yes"})

    assert manager.answered == [("q_open", "yes", True, T0)]


@pytest.mark.parametrize("text", [None, 7, ["How many?"]])
async def test_a_non_string_ask_text_is_a_400(db, text):
    """Otherwise the glasses read out the string "None"."""
    manager = StubManager()

    async with client_for(db, manager) as http:
        assert (await http.post("/api/ask", json={"text": text})).status_code == 400
    assert manager.asked == []


@pytest.mark.parametrize("episode_id", [None, 7, {"id": "e_1"}])
async def test_a_wrong_typed_episode_id_is_a_400(db, episode_id):
    """Dropping it would ask about an episode the answer could never reach (§8.3)."""
    manager = StubManager()

    async with client_for(db, manager) as http:
        response = await http.post(
            "/api/ask", json={"text": "How many?", "episode_id": episode_id}
        )

    assert response.status_code == 400
    assert manager.asked == []


async def test_an_omitted_episode_id_is_still_fine(db):
    manager = StubManager(row=make_question("q_new"))

    async with client_for(db, manager) as http:
        assert (await http.post(
            "/api/ask", json={"text": "Is that yours?"}
        )).status_code == 200
    assert manager.asked[0][2] is None


# -- one query, not one per episode (§8.3) --------------------------------


async def test_the_reported_projection_costs_one_query(db, monkeypatch):
    """A day view with N episodes must not be N round trips through the DB lock.

    The tick loop writes under the same lock, so a dashboard poll that scaled
    with the episode count would contend with capture for no reason.
    """
    for i in range(3):
        _episode(db, f"e_000{i}")
        db.insert_question(make_question(
            f"q_{i}", created_t=T0 + i, episode_id=f"e_000{i}", status="answered",
            answer_t=T0 + i, parsed={"confirmed": True, "note": f"note {i}"},
        ))

    calls: list[int] = []
    real = db.list_questions

    def counted(limit: int = 20):
        calls.append(limit)
        return real(limit)

    def per_episode(episode_id: str):
        raise AssertionError(f"N+1: one query per episode ({episode_id})")

    monkeypatch.setattr(db, "list_questions", counted)
    monkeypatch.setattr(db, "questions_for_episode", per_episode)

    async with client_for(db) as http:
        rows = (await http.get(f"/api/episodes?day={DAY}")).json()

    assert len(calls) == 1, f"expected one questions query, got {len(calls)}"
    assert {r["id"]: r["reported"]["note"] for r in rows} == {
        "e_0000": "note 0", "e_0001": "note 1", "e_0002": "note 2",
    }


async def test_an_episode_beyond_the_projection_scan_reports_nothing(db, monkeypatch):
    """The bounded scan degrades to `null`, never to another episode's answer."""
    monkeypatch.setattr("pipeline.api.routes.REPORTED_SCAN", 1)
    _episode(db, "e_old")
    _episode(db, "e_new")
    db.insert_question(make_question("q_old", created_t=T0, episode_id="e_old",
                                     status="answered", answer_t=T0,
                                     parsed={"note": "old"}))
    db.insert_question(make_question("q_new", created_t=T0 + 9, episode_id="e_new",
                                     status="answered", answer_t=T0 + 9,
                                     parsed={"note": "new"}))

    async with client_for(db) as http:
        rows = {r["id"]: r["reported"] for r in
                (await http.get(f"/api/episodes?day={DAY}")).json()}

    assert rows["e_new"]["note"] == "new"
    assert rows["e_old"] is None
