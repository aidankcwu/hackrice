"""A17 — `speak(text, urgency)`. Done when B can call one function and A owns the rest.

The contract these pin down is the one §13.3 states: "B decides whether and what; A owns
how." Everything here is about what `speak` must *not* do — not rate-limit, not decide,
and above all not raise, because B calls it from inside action handling and a dead
socket must not propagate into the reasoner.
"""

from __future__ import annotations

import asyncio

import pytest

from longevity import speak as speak_mod
from longevity import wire
from longevity.speak import DEFAULT_URGENCY, URGENCIES, Speaker, default_speaker, speak

TEXT = "You have been at a screen for 90 minutes."


class FakeLink:
    """Models `GlassesLink.send_text`: returns how many phones got it, never raises."""

    def __init__(self, phones: int = 1, explode: type[BaseException] | None = None) -> None:
        self.phones = phones
        self.explode = explode
        self.sent: list[str] = []

    async def send_text(self, message: str) -> int:
        if self.explode is not None:
            raise self.explode("socket closed")
        self.sent.append(message)
        return self.phones


@pytest.fixture(autouse=True)
def _reset_singleton():
    """`default_speaker` is process-wide; tests must not leak a binding into each other."""
    speak_mod._DEFAULT = None
    yield
    speak_mod._DEFAULT = None


# --- the happy path -----------------------------------------------------------


async def test_delivered_utterance_reports_true():
    link = FakeLink(phones=1)
    assert await Speaker(link).speak(TEXT) is True


async def test_what_goes_on_the_wire_is_a_valid_speak_message():
    link = FakeLink()
    await Speaker(link).speak(TEXT, "high")

    msg = wire.decode(link.sent[0])
    assert msg["type"] == wire.SPEAK
    assert msg["text"] == TEXT
    assert msg["urgency"] == "high"


@pytest.mark.parametrize("urgency", URGENCIES)
async def test_every_urgency_rides_through_untouched(urgency: str):
    """§4.4 delivers utterances "with urgency level"; A must not flatten B's intent."""
    link = FakeLink()
    await Speaker(link).speak(TEXT, urgency)
    assert wire.decode(link.sent[0])["urgency"] == urgency


# --- what speak() must NOT do -------------------------------------------------


async def test_repeated_calls_are_never_rate_limited():
    """The whole point of A17's split: the limiter lives on B's side, not here.

    If this module silently swallowed repeats, B could not distinguish a rate-limited
    decision from a broken socket, and the dashboard's silent-decision feed would lie.
    """
    link = FakeLink()
    sp = Speaker(link)
    for i in range(50):
        assert await sp.speak(f"utterance {i}") is True

    assert len(link.sent) == 50
    assert sp.spoken == 50


async def test_speak_does_not_decide_whether_to_speak():
    """Called means say it — even for text a limiter would obviously suppress."""
    link = FakeLink()
    sp = Speaker(link)
    for _ in range(5):
        await sp.speak(TEXT)  # the identical utterance five times running
    assert len(link.sent) == 5


# --- never raises -------------------------------------------------------------


async def test_no_phone_connected_is_false_not_an_exception():
    """The socket is fine, nobody is wearing the glasses. B logs it; T0 keeps running."""
    link = FakeLink(phones=0)
    sp = Speaker(link)
    assert await sp.speak(TEXT) is False
    assert sp.undelivered == 1
    assert link.sent, "the message was still sent; it simply reached nobody"


async def test_unbound_speaker_is_false_not_an_exception():
    sp = Speaker(None)
    assert await sp.speak(TEXT) is False
    assert sp.undelivered == 1


async def test_send_failure_is_swallowed():
    """A dead socket must not propagate up into B's reasoner."""
    sp = Speaker(FakeLink(explode=RuntimeError))
    assert await sp.speak(TEXT) is False
    assert sp.undelivered == 1


async def test_cancellation_is_not_swallowed():
    """Shutdown must still work.

    `CancelledError` is a BaseException, so the broad `except Exception` in `speak`
    does not catch it. If someone ever widens that to `except BaseException`, Ctrl-C
    during an utterance would hang, and this is the test that would say so.
    """
    sp = Speaker(FakeLink(explode=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await sp.speak(TEXT)


# --- input handling -----------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
async def test_empty_text_sends_nothing(text: str):
    link = FakeLink()
    assert await Speaker(link).speak(text) is False
    assert link.sent == []


async def test_text_is_stripped():
    link = FakeLink()
    await Speaker(link).speak(f"  {TEXT}\n")
    assert wire.decode(link.sent[0])["text"] == TEXT


async def test_unknown_urgency_falls_back_but_still_speaks():
    """A bad urgency from B is not a reason to stay silent."""
    link = FakeLink()
    assert await Speaker(link).speak(TEXT, "URGENT!!") is True
    assert wire.decode(link.sent[0])["urgency"] == DEFAULT_URGENCY


# --- counters and the process-wide speaker ------------------------------------


async def test_counters_separate_delivered_from_undelivered():
    sp = Speaker(FakeLink(phones=1))
    await sp.speak(TEXT)
    sp.bind(FakeLink(phones=0))
    await sp.speak(TEXT)

    assert sp.stats() == {"spoken": 1, "undelivered": 1, "bound": True}


async def test_default_speaker_is_a_singleton():
    assert default_speaker() is default_speaker()


async def test_module_level_speak_uses_the_bound_default_speaker():
    """This is literally what B imports, so bind-then-call must work end to end."""
    link = FakeLink()
    default_speaker().bind(link)

    assert await speak(TEXT, "low") is True
    assert wire.decode(link.sent[0])["text"] == TEXT


async def test_module_level_speak_is_safe_before_anything_is_bound():
    """B may call before the socket exists. That is a dropped utterance, not a crash."""
    assert await speak(TEXT) is False
