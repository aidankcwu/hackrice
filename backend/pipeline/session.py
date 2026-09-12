"""Judge-session lifecycle and wearer-state reset."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from .actions.speech import SpeechLimiter
from .db import Database
from .episodes.builder import EpisodeBuilder
from .gate.gate import TriggerGate
from .models import Session
from .reasoner.reasoner import Reasoner

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, db: Database, gate: TriggerGate, speech: SpeechLimiter,
                 episodes: EpisodeBuilder, clock: Callable[[], float] = time.time,
                 reasoner: Reasoner | None = None) -> None:
        self.db = db
        self.gate = gate
        self.speech = speech
        self.episodes = episodes
        self.clock = clock
        self.reasoner = reasoner

    def start(self, name: str = "") -> Session:
        now = self.clock()
        current = self.db.current_session()
        if current is not None:
            self.db.end_session(current.id, now)
        self.gate.reset()
        self.speech.reset()
        if self.reasoner is not None:
            self.reasoner.bump_epoch()
        self.episodes.reset(now)
        self.db.mark_all_pending_fired()
        session = Session(
            id=f"s_{uuid.uuid4().hex[:8]}", name=name, started_t=now
        )
        self.db.insert_session(session)
        log.info("judge session started: id=%s name=%r", session.id, session.name)
        return session

    def end(self) -> Session | None:
        session = self.db.current_session()
        if session is None:
            return None
        self.db.end_session(session.id, self.clock())
        return self.db.get_session(session.id)

    def current(self) -> Session | None:
        return self.db.current_session()
