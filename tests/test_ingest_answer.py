"""The phone half of ask/answer: capability negotiation and the answer callback.

ASK_DESIGN §3, §8.4, §8.7. Three things are pinned here, in descending order of
how much they would cost to get wrong on the venue floor:

1. **A phone that did not advertise `ask` never gets asked.** The degradation
   otherwise is silent and awful — the glasses read a question out loud and the
   microphone never opens, so the wearer answers into nothing and the row sits
   open until it expires.
2. **The answer is stamped on the Mac's clock.** Expiry is computed on the Mac,
   so an answer carrying the phone's clock could arrive "late" from a wearer who
   replied instantly (§8.4).
3. **Nothing on this path can kill the socket** — not a malformed answer, not a
   handler that raises. The glasses reconnecting is expensive and DAT
   availability is transient (hardware_software.md §24).
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from longevity import vlm, wire
from longevity.server import ingest

QID = "q_deadbeef"


class FakeSocket:
    """The parts of a Starlette WebSocket `glasses_ws` actually touches."""

    def __init__(self, *messages: str) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.client = SimpleNamespace(host="test-phone")
        self._events = [{"type": "websocket.receive", "text": m} for m in messages]
        self._events.append({"type": "websocket.disconnect"})
        self.closed = None

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict:
        return self._events.pop(0)

    async def close(self, code: int) -> None:
        self.closed = code


# --- capability negotiation (§8.7) --------------------------------------------


def test_a_phone_that_advertises_ask_supports_it() -> None:
    link = ingest.GlassesLink()
    socket = object()
    link.clients.add(socket)

    ingest._handle(link, wire.hello_message("phone", ["ask"]), socket)

    assert link.supports("ask") is True


def test_a_hello_without_caps_supports_nothing() -> None:
    """The old phone build. It speaks, it just cannot listen."""
    link = ingest.GlassesLink()
    socket = object()
    link.clients.add(socket)

    ingest._handle(link, json.dumps({"v": 1, "type": "hello", "device": "phone"}), socket)

    assert link.supports("ask") is False


def test_garbage_caps_are_ignored_not_trusted() -> None:
    link = ingest.GlassesLink()
    socket = object()
    link.clients.add(socket)

    ingest._handle(
        link,
        json.dumps({"v": 1, "type": "hello", "caps": ["ask", 7, None, {"x": 1}, ""]}),
        socket,
    )

    assert link.caps[id(socket)] == {"ask"}
    assert link.supports("ask") is True


def test_caps_that_are_not_a_list_leave_the_phone_incapable() -> None:
    link = ingest.GlassesLink()
    socket = object()
    link.clients.add(socket)

    ingest._handle(link, json.dumps({"v": 1, "type": "hello", "caps": "ask"}), socket)

    assert link.supports("ask") is False


def test_an_unconnected_socket_cannot_vouch_for_capabilities() -> None:
    """`supports` reads the *live* sockets, so a stale entry cannot speak for one."""
    link = ingest.GlassesLink()
    link.caps[id(object())] = {"ask"}

    assert link.supports("ask") is False


async def test_caps_are_cleared_when_the_phone_goes_away() -> None:
    """Capabilities describe a connection, not a phone (§8.7)."""
    socket = FakeSocket(wire.hello_message("phone", ["ask"]))

    await ingest.glasses_ws(socket)
    link = socket.app.state.glasses_link

    assert link.caps == {}
    assert link.supports("ask") is False
    assert link.n_disconnects == 1


async def test_the_stats_block_reports_the_live_caps() -> None:
    link = ingest.GlassesLink()
    socket = object()
    link.clients.add(socket)
    ingest._handle(link, wire.hello_message("phone", ["ask", "speak"]), socket)

    assert link.stats()["caps"] == ["ask", "speak"]
    assert link.stats()["answers"] == 0


# --- picking one connection for the exchange (§8.2, §8.7) ---------------------
#
# A question is an exchange, not a broadcast: the audio, the `ask` that opens the
# microphone and the answer that comes back all belong to one socket. Sending the
# two halves to "every phone" is indistinguishable from correct with one phone
# connected and splits the exchange the moment a second one exists.


class SendableSocket:
    """A socket that records what it was sent, or refuses to send at all."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise ConnectionResetError("socket is gone")
        self.sent.append(message)


def test_pick_returns_a_socket_that_advertises_the_cap() -> None:
    link = ingest.GlassesLink()
    socket = object()
    link.add_client(socket)
    ingest._handle(link, wire.hello_message("phone", ["ask"]), socket)

    assert link.pick("ask") is socket
    assert link.pick() is socket, "no cap asked for means any connected socket"


def test_pick_skips_a_phone_that_cannot_listen() -> None:
    link = ingest.GlassesLink()
    mute = object()
    link.add_client(mute)
    ingest._handle(link, wire.hello_message("phone", ["speak"]), mute)

    assert link.pick("ask") is None
    assert link.pick() is mute, "it is still a phone, just not one to ask"


def test_pick_has_nothing_to_offer_with_no_phone() -> None:
    assert ingest.GlassesLink().pick("ask") is None
    assert ingest.GlassesLink().pick() is None


def test_pick_prefers_the_newest_connection() -> None:
    """A phone that reconnected owns the wearer's ears; the stale socket does not."""
    link = ingest.GlassesLink()
    old, new = object(), object()
    link.add_client(old)
    ingest._handle(link, wire.hello_message("phone", ["ask"]), old)
    link.add_client(new)
    ingest._handle(link, wire.hello_message("phone", ["ask"]), new)

    assert link.pick("ask") is new


def test_supports_and_pick_cannot_disagree() -> None:
    """Admission asks `supports`, delivery asks `pick`; a gap between them would
    admit a question nothing can carry."""
    link = ingest.GlassesLink()
    socket = object()
    link.add_client(socket)
    ingest._handle(link, wire.hello_message("phone", ["ask"]), socket)

    assert link.supports("ask") is (link.pick("ask") is not None)
    link.drop_client(socket)
    assert link.supports("ask") is (link.pick("ask") is not None) is False


async def test_send_to_reaches_exactly_one_socket() -> None:
    link = ingest.GlassesLink()
    chosen, other = SendableSocket(), SendableSocket()
    link.add_client(chosen)
    link.add_client(other)

    assert await link.send_to(chosen, "hello") is True
    assert chosen.sent == ["hello"]
    assert other.sent == [], "send_to is not a broadcast"


async def test_a_failed_send_to_drops_the_socket_and_its_caps() -> None:
    """The second half of an exchange must not be offered the socket that just died."""
    link = ingest.GlassesLink()
    dead = SendableSocket(fail=True)
    link.add_client(dead)
    link.caps[id(dead)] = {"ask"}

    assert await link.send_to(dead, "hello") is False
    assert link.clients == set()
    assert link.caps == {}
    assert link.pick("ask") is None
    assert link.supports("ask") is False


async def test_a_failed_send_to_never_raises_at_the_caller() -> None:
    """The ask path runs as a task the manager owns; a dead socket is a False."""
    link = ingest.GlassesLink()
    link.add_client(SendableSocket(fail=True))

    assert await link.send_to(next(iter(link.clients)), "x") is False


# --- the answer callback (§8.4) -----------------------------------------------


def _answers(link: ingest.GlassesLink) -> list[tuple]:
    seen: list[tuple] = []
    link.on_answer = lambda *args: seen.append(args)
    return seen


def test_an_answer_reaches_the_handler_and_is_counted() -> None:
    link = ingest.GlassesLink()
    seen = _answers(link)

    ingest._handle(link, wire.answer_message(QID, "yeah, two", heard=True))

    assert link.n_answers == 1
    assert link.n_malformed == 0
    qid, text, heard, _t = seen[0]
    assert (qid, text, heard) == (QID, "yeah, two", True)


async def test_an_answer_from_a_socket_the_question_was_never_sent_to_is_ignored() -> None:
    """A second connected phone — or a stale one not yet reaped — must not be
    able to finalize a question `pick` sent to someone else (ASK_DESIGN §8.2:
    one socket carries the whole exchange; `claim_answer` otherwise accepts by
    question id alone, so anything with the id could win)."""
    link = ingest.GlassesLink()
    client_a, client_b = SendableSocket(), SendableSocket()
    link.add_client(client_a)
    link.add_client(client_b)
    seen = _answers(link)

    assert await link.send_to(client_a, wire.ask_message(QID, 8.0, "yes_no", "Beer?"))

    ingest._handle(link, wire.answer_message(QID, "nope, water", heard=True), client_b)
    assert seen == [], "client B never got this question and must not be able to answer it"
    assert link.n_answers == 0

    ingest._handle(link, wire.answer_message(QID, "yeah, one", heard=True), client_a)
    assert len(seen) == 1
    qid, text, heard, _t = seen[0]
    assert (qid, text, heard) == (QID, "yeah, one", True)
    assert link.n_answers == 1


def test_the_answer_is_stamped_on_the_macs_clock_not_the_phones() -> None:
    """§8.4: the phone's `t` is metadata. Expiry is computed on this machine."""
    link = ingest.GlassesLink()
    seen = _answers(link)
    before = time.time()

    ingest._handle(link, wire.answer_message(QID, "yes", t=1_000.0))

    assert seen[0][3] >= before
    assert seen[0][3] != 1_000.0


def test_heard_false_survives_as_false() -> None:
    """"Nothing was said" must not arrive as "" and be mistaken for a bad mic."""
    link = ingest.GlassesLink()
    seen = _answers(link)

    ingest._handle(link, wire.answer_message(QID, "", heard=False))

    assert seen[0][1:3] == ("", False)


def test_a_missing_heard_defaults_to_true() -> None:
    link = ingest.GlassesLink()
    seen = _answers(link)

    ingest._handle(link, json.dumps({"v": 1, "type": "answer", "question_id": QID}))

    assert seen[0][1:3] == ("", True)


def test_a_non_boolean_heard_is_malformed_and_never_reaches_the_manager() -> None:
    """Present-but-wrong is not the same as absent.

    Absent means an older phone build that never sends `heard`, and a transcript
    is evidence enough. A `heard` the Mac cannot read means the phone tried to
    say something about the microphone; coercing it to True hands the parser an
    empty transcript as "the wearer said nothing", which §8.8 finalises as a real
    answer to a question that may never have been heard at all.
    """
    link = ingest.GlassesLink()
    seen = _answers(link)

    for value in ("false", 0, 1, None, [], {"heard": True}):
        ingest._handle(link, json.dumps(
            {"v": 1, "type": "answer", "question_id": QID, "text": "yes",
             "heard": value}
        ))

    assert link.n_malformed == 6
    assert link.n_answers == 0
    assert seen == []


def test_an_answer_with_no_question_id_is_malformed_not_fatal() -> None:
    link = ingest.GlassesLink()
    seen = _answers(link)

    ingest._handle(link, json.dumps({"v": 1, "type": "answer", "text": "two"}))
    ingest._handle(link, json.dumps({"v": 1, "type": "answer", "question_id": 7}))
    ingest._handle(
        link, json.dumps({"v": 1, "type": "answer", "question_id": QID, "text": 7})
    )

    assert link.n_malformed == 3
    assert link.n_answers == 0
    assert seen == []


def test_an_answer_with_nothing_bound_is_a_warning_not_a_crash() -> None:
    """The manager may not be wired yet; the socket must not care."""
    link = ingest.GlassesLink()

    ingest._handle(link, wire.answer_message(QID, "two"))

    assert link.n_answers == 1


def test_a_raising_handler_never_reaches_the_socket() -> None:
    link = ingest.GlassesLink()

    def explode(*_args):
        raise RuntimeError("the manager is broken")

    link.on_answer = explode
    ingest._handle(link, wire.answer_message(QID, "two"))

    assert link.n_answers == 1


async def test_the_socket_survives_a_bad_answer_and_keeps_reading() -> None:
    socket = FakeSocket(
        json.dumps({"v": 1, "type": "answer"}),
        wire.hello_message("phone", ["ask"]),
        wire.answer_message(QID, "two"),
    )

    await ingest.glasses_ws(socket)
    link = socket.app.state.glasses_link

    assert link.n_malformed == 1
    assert link.n_answers == 1, "the reader kept going past the bad message"


# --- what goes on the wire (§3) -----------------------------------------------


def test_ask_message_round_trips() -> None:
    msg = wire.decode(wire.ask_message(QID, 8.0, "count", "How many?"))

    assert msg == {
        "v": 1, "type": "ask", "question_id": QID,
        "listen_s": 8.0, "answer_kind": "count", "text": "How many?",
    }


def test_answer_message_round_trips() -> None:
    msg = wire.decode(wire.answer_message(QID, "just the one", heard=True, t=12.5))

    assert msg == {
        "v": 1, "type": "answer", "question_id": QID,
        "text": "just the one", "heard": True, "t": 12.5,
    }


def test_the_ask_and_answer_types_are_distinct_from_speech() -> None:
    """`ask` is the instruction, not the utterance — the audio still rides `audio`."""
    assert {wire.ASK, wire.ANSWER}.isdisjoint({wire.SPEAK, wire.AUDIO, wire.CAPTURE})


# --- the fake tagger override (§8.10) -----------------------------------------
#
# This is what makes a key-less run reach the ask path at all: the gate only wakes
# on what T0 reports, and a constant "office/seated" can never produce an alcohol
# sighting to ask about.


def test_fake_fields_default_when_nothing_is_set() -> None:
    assert vlm.fake_fields({}) == vlm.DEFAULT_FAKE_FIELDS


def test_t0_fake_fields_merges_over_the_defaults() -> None:
    fields = vlm.fake_fields(
        {"T0_FAKE_FIELDS": '{"alcohol_visible": true, "scene": "restaurant"}'}
    )

    assert fields["alcohol_visible"] is True
    assert fields["scene"] == "restaurant"
    assert fields["activity"] == "seated", "untouched defaults survive the merge"


def test_invalid_t0_fake_fields_warns_and_falls_back(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert vlm.fake_fields({"T0_FAKE_FIELDS": "{not json"}) == vlm.DEFAULT_FAKE_FIELDS
        assert vlm.fake_fields({"T0_FAKE_FIELDS": "[1, 2]"}) == vlm.DEFAULT_FAKE_FIELDS
    assert caplog.text.count("T0_FAKE_FIELDS") == 2


async def test_the_override_reaches_every_tag(monkeypatch) -> None:
    monkeypatch.setenv("T0_FAKE_FIELDS", '{"alcohol_visible": true}')
    client = vlm.build_client("fake")

    assert (await client.tag(b"jpeg"))["alcohol_visible"] is True
    assert (await client.tag(b"jpeg"))["alcohol_visible"] is True


async def test_a_mutated_result_cannot_poison_the_next_tick() -> None:
    """`tag` hands out a copy; a consumer editing it must not change the source."""
    client = vlm.FakeClient(latency=0)
    first = await client.tag(b"jpeg")
    first["scene"] = "trail"

    assert (await client.tag(b"jpeg"))["scene"] == "office"
