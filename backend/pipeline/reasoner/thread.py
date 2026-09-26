"""One continuing conversation per session for the clerk.

Every wake-up used to be a fresh prompt: persona, today's one-line notes, a
minute of tags, two frames. The model could not see what it had thought last
time, so a water bottle that it had already decided was furniture looked new
again ten wake-ups later, and "I already said that" had to be dug out of the
notes. This module keeps the session as one conversation instead:

    system      persona + job (+ what was learned)
    user/asst   the last ``max_turns`` wake-ups, raw: what it was shown and
                what it replied (its thinking, its reading, its actions)
    user        Running picture: the model's own summary of turns that have
                aged out, plus the one-line residue of any turn that aged out
                since the model last rewrote it; then this wake-up

Images ride only on the newest ``image_turns`` turns; older turns keep their
text. When a turn falls off the far end it is not lost: its one-line residue
joins the picture block until the model's next reply, whose ``summary`` field
is the picture rewritten with it folded in. The window is therefore always
full and the picture always current, and nothing leaves the context without
first passing through the summary.

The thread lives in memory. After a restart it is rebuilt from the decisions
table (text only, no frames) and the persisted picture, so a session survives
a container bounce with its memory intact.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

__all__ = ["SessionThread", "Turn", "THREAD_TURNS", "THREAD_IMAGE_TURNS",
           "PICTURE_HEADING", "render_reply", "residue_line"]

#: Raw turns kept. Twenty covers a fifteen-minute session at one wake-up
#: every forty seconds without any folding at all.
THREAD_TURNS = 20
#: Newest turns that keep their frames. Older turns keep tags and text.
THREAD_IMAGE_TURNS = 3

PICTURE_HEADING = "Running picture of this session (your own words, rewritten each reply):"
PICTURE_EMPTY = "nothing yet: this is the first wake-up of the session"
AGED_HEADING = ("Aged out of the raw window since you last rewrote the picture; "
                "fold these into your next `summary`:")

#: Longest reply text kept per raw turn. A reply is thinking + reading +
#: actions; past this it is the model repeating itself.
REPLY_MAX_CHARS = 1200


@dataclass
class Turn:
    """One wake-up as it was shown and answered."""

    t: float
    #: The user content exactly as sent (may carry ``input_image`` items).
    user: list[dict[str, Any]]
    #: The model's reply rendered as text (see :func:`render_reply`).
    assistant: str
    #: One line for the aged-out list.
    residue: str
    #: Set when the turn was rebuilt from the database after a restart.
    rebuilt: bool = False


def _strip_images(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same content with every image (and its label) removed."""

    out: list[dict[str, Any]] = []
    stripped = 0
    for item in content:
        if item.get("type") == "input_image":
            # The label before an image describes it; without the image it
            # is a dangling "frame at t-3s" line. Drop it too.
            if out and out[-1].get("type") == "input_text" \
                    and str(out[-1].get("text", "")).startswith(("t-", "t+", "trigger frame")):
                out.pop()
            stripped += 1
            continue
        out.append(item)
    if stripped:
        out.append({"type": "input_text",
                    "text": f"({stripped} frame(s) omitted: older turn)"})
    return out


def render_reply(resp: Any) -> str:
    """The reply as the model will read it back: thinking, reading, actions.

    Compact JSON, not the full schema: the ``summary`` is carried by the
    picture block, and action arguments the model does not need to re-read
    (``fills``, ``args``) are dropped. Bounded by :data:`REPLY_MAX_CHARS`.
    """

    actions: list[dict[str, Any]] = []
    for action in getattr(resp, "actions", None) or []:
        kind = getattr(action, "type", None)
        if kind is None and isinstance(action, dict):
            kind = action.get("type")
        row: dict[str, Any] = {"type": kind}
        for key in ("text", "line", "question", "reason", "condition", "category"):
            value = getattr(action, key, None) if not isinstance(action, dict) else action.get(key)
            if isinstance(value, str) and value:
                row[key] = value[:200]
        actions.append(row)
    body = {
        "thinking": (getattr(resp, "thinking", None) or "")[:600],
        "interpretation": getattr(resp, "interpretation", "") or "",
        "actions": actions,
    }
    text = json.dumps(body, ensure_ascii=False)
    return text if len(text) <= REPLY_MAX_CHARS else text[:REPLY_MAX_CHARS - 1] + "…"


def residue_line(t_label: str, trigger: str, resp: Any) -> str:
    """The one line an aged-out turn leaves in the picture block."""

    said = [getattr(a, "text", "") for a in (getattr(resp, "actions", None) or [])
            if getattr(a, "type", None) in ("speak", "ask") and getattr(a, "text", "")]
    tail = f' -> handed off: "{said[0][:80]}"' if said else ""
    reading = (getattr(resp, "interpretation", "") or "").strip()
    return f"{t_label} {trigger}: {reading[:120]}{tail}"


class SessionThread:
    """The conversation for one session: raw window, picture, residue."""

    def __init__(self, max_turns: int = THREAD_TURNS,
                 image_turns: int = THREAD_IMAGE_TURNS) -> None:
        self.max_turns = max(1, int(max_turns))
        self.image_turns = max(0, int(image_turns))
        self.session_id: str | None = None
        self.summary: str = ""
        self.aged: list[str] = []
        self.turns: deque[Turn] = deque()
        self.resets = 0
        self.folded = 0

    # -- lifecycle ----------------------------------------------------------

    def reset(self, session_id: str | None = None) -> None:
        self.session_id = session_id
        self.summary = ""
        self.aged = []
        self.turns.clear()
        self.resets += 1

    def bind(self, session_id: str | None) -> bool:
        """Point the thread at ``session_id``; ``True`` when that was a change
        (and the thread was reset for it)."""

        if session_id == self.session_id:
            return False
        self.reset(session_id)
        return True

    @property
    def empty(self) -> bool:
        return not self.turns and not self.summary and not self.aged

    # -- building the input ---------------------------------------------------

    def picture_block(self) -> str:
        lines = [PICTURE_HEADING, self.summary.strip() or PICTURE_EMPTY]
        if self.aged:
            lines.append("")
            lines.append(AGED_HEADING)
            lines.extend(f"  {line}" for line in self.aged)
        return "\n".join(lines)

    def messages(self, system_text: str, user_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The Responses API ``input`` for this wake-up."""

        # Order is for the prompt cache: system, then the raw turns (a stable,
        # growing prefix), then the picture (rewritten every reply) and this
        # wake-up. With the picture second, every earlier turn sat behind a
        # changed block and was re-read cold on every call (~15 K tokens, seen
        # live: cached=2816 of in=17370).
        out: list[dict[str, Any]] = [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
        ]
        n = len(self.turns)
        for index, turn in enumerate(self.turns):
            keep_images = index >= n - self.image_turns
            content = turn.user if keep_images else _strip_images(turn.user)
            out.append({"role": "user", "content": content})
            out.append({"role": "assistant",
                        "content": [{"type": "output_text", "text": turn.assistant}]})
        out.append({"role": "user", "content": [
            {"type": "input_text", "text": self.picture_block()}, *user_content]})
        return out

    # -- recording a reply ------------------------------------------------------

    def commit(self, turn: Turn, summary: str | None) -> None:
        """Append the finished turn, age the window, absorb the new picture."""

        self.turns.append(turn)
        while len(self.turns) > self.max_turns:
            old = self.turns.popleft()
            self.aged.append(old.residue)
            self.folded += 1
        text = (summary or "").strip()
        if text:
            # The model was shown the aged lines and asked to fold them in;
            # a rewritten picture supersedes them.
            self.summary = text
            self.aged = []

    # -- restart ------------------------------------------------------------------

    def rebuild(self, decisions: Iterable[Any], summary: str | None,
                label_t: Any) -> int:
        """Refill the raw window from stored decisions (text only).

        ``decisions`` are this session's rows, oldest first; dropped rows are
        skipped. ``label_t`` renders a decision's time. Returns the number of
        turns rebuilt. Frames are gone with the ring buffer; the model is told.
        """

        self.turns.clear()
        self.aged = []
        self.summary = (summary or "").strip()
        count = 0
        for d in decisions:
            if getattr(d, "dropped", False):
                continue
            when = label_t(d.t)
            reply = json.dumps({
                "thinking": (getattr(d, "thinking", "") or "")[:600],
                "interpretation": d.interpretation,
                "actions": [{k: v for k, v in a.items()
                             if k in ("type", "text", "line", "question", "reason")}
                            for a in (d.actions or [])],
            }, ensure_ascii=False)
            turn = Turn(
                t=d.t,
                user=[{"type": "input_text",
                       "text": f"Trigger: {d.trigger} at {when}\n"
                               "(rebuilt after a restart: the tags and frames of "
                               "this wake-up were not kept)"}],
                assistant=reply[:REPLY_MAX_CHARS],
                residue=f"{when} {d.trigger}: {d.interpretation[:120]}",
                rebuilt=True,
            )
            self.turns.append(turn)
            count += 1
        while len(self.turns) > self.max_turns:
            old = self.turns.popleft()
            self.aged.append(old.residue)
        return count

    def stats(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turns": len(self.turns),
            "max_turns": self.max_turns,
            "image_turns": self.image_turns,
            "summary_chars": len(self.summary),
            "aged_pending": len(self.aged),
            "folded": self.folded,
            "resets": self.resets,
        }
