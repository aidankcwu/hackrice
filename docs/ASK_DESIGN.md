# Ask / answer — the glasses can ask one question and hear the answer

Status: design contract for the `ask-answer/batch-1` branch. Every subtask codes
against the names in §3–§5; change a name here first, then everywhere.

## 1. What it does

T1 (GPT) may now emit an `ask` action alongside its other actions. Code decides
whether the question is allowed (§4), speaks it through the glasses, then tells
the phone to open the glasses' microphone for a short answer window. The phone
transcribes on-device and sends the text back. GPT reads the answer and returns
a structured update that is written onto the episode that prompted the question,
plus a memory line and a decision row so the dashboard shows the whole exchange.

Gemini (T0) never asks. It tags frames and nothing else.

Unanswered questions expire quietly. Silence is never a yes.

## 2. Flow

```
gate wakes T1 (e.g. trigger alcohol_seen on an alcohol_sighting episode)
  └─ T1 returns actions [annotate, ask{text:"Is that yours?", answer_kind:"yes_no", fills:"confirmed"}]
       └─ ActionHandler → QuestionManager.ask(...)
            ├─ guards (§4) fail → decision row shows the ask as suppressed; nothing spoken
            └─ guards pass → pending_questions row (open)
                            → speak(text) via the existing speak fn (ElevenLabs/AVSpeech)
                            → wire.ask_message(question_id, listen_s) to the phone
phone: waits for playback to end, opens mic (hands-free profile), on-device STT for listen_s
  └─ wire.answer_message(question_id, text, heard) → Mac ingest → QuestionManager.on_answer()
       ├─ parse (GPT with a strict schema, or regex under --reasoner fake)
       ├─ episode.dominant += {confirmed, count, food_type, note} as answered
       ├─ annotate line + decision row (trigger "answer:<question_id>")
       └─ at most ONE follow-up ask (e.g. "How many?"), chained by followup_of
tick loop: QuestionManager.expire(now) marks open rows past expires_t as expired
```

## 3. Wire (`src/longevity/wire.py`) — additive

| Type | Direction | Fields |
|---|---|---|
| `ask` | Mac → phone | `question_id`, `listen_s` (float), `answer_kind` (`yes_no`\|`count`\|`free`), `text` (the question, for display) |
| `answer` | phone → Mac | `question_id`, `text` (transcript, may be ""), `heard` (bool), `t` |

The spoken audio still travels as today's `speak`/`audio` message, sent
immediately before `ask`. The phone must start listening only after playback of
that audio finishes, then for `listen_s` seconds or until a pause.

## 4. Guards (code decides, the model proposes)

All in `Timings` (`backend/pipeline/config.py`), demo / production:

| Field | demo | prod | Meaning |
|---|---|---|---|
| `ask_min_gap` | 30 | 300 | seconds after an ask fires before another may (the "20 frames" rule at 1.5 s ticks) |
| `ask_max_per_hour` | 10 | 6 | hourly cap |
| `ask_listen_s` | 8 | 8 | answer window the phone is told to open |
| `ask_expire_s` | 20 | 20 | open → expired this long after asking (≥ listen_s + playback) |
| `ask_followup_max` | 1 | 1 | follow-ups allowed per original question |

Plus, not configurable:
- at most one `open` question at any time;
- never two questions for the same `episode_id`;
- an ask also needs `SpeechLimiter.allow(t)` (a question must not land two seconds after a statement) and, when granted, counts as that utterance;
- a T1 wake-up that is itself an answer (`trigger` starts with `answer:`) may ask only the one follow-up;
- answers can only set the fields in §5; the model cannot invent a new one.

A suppressed ask is recorded on the decision (`actions` keeps it, `asked=False`,
`ask_suppressed_reason`) so the feed shows the system chose not to ask.

## 5. Data (`backend/pipeline/models.py`, `db.py`)

```python
class AskAction(_ActionBase):            # reasoner/schema.py
    type: Literal["ask"] = "ask"
    text: str                             # the question, one sentence, read aloud
    answer_kind: Literal["yes_no", "count", "free"] = "yes_no"
    fills: Literal["confirmed", "count", "food_type", "note"] = "confirmed"
    reason: str = ""

class PendingQuestion(BaseModel):         # models.py
    id: str                               # "q_" + 8 hex
    created_t: float
    expires_t: float
    decision_id: str | None
    episode_id: str | None
    question: str
    answer_kind: str
    fills: str
    status: Literal["open", "answered", "expired", "suppressed"] = "open"
    answer_text: str | None = None
    answer_t: float | None = None
    parsed: dict[str, Any] = {}           # AnswerParse.model_dump()
    followup_of: str | None = None

class AnswerParse(BaseModel):             # reasoner/schema.py
    understood: bool
    confirmed: bool | None = None
    count: float | None = None
    food_type: str | None = None          # must be in models.FOOD_TYPES or None
    note: str = ""                        # ≤ 80 chars, what the wearer said in effect
    followup: str | None = None           # one more question, or None
```

DB: table `pending_questions` mirroring `PendingQuestion` (parsed as JSON text),
index on `(status, expires_t)`. Methods on `Database`:
`insert_question`, `update_question`, `get_question(id)`, `open_question()`,
`questions_for_episode(episode_id)`, `list_questions(limit)`,
`expire_questions(now) -> int`, and `claim_answer(question_id, text, heard, t) -> bool` (atomic `UPDATE … WHERE status='open'`, True iff this call claimed it).

Episode write-back: **none** (superseded by §8.3). `update_episode_dominant`
is not built. Consumers read the question rows for an episode.

## 6. Modules and owners

| Subtask | Owner | Files (exclusive) |
|---|---|---|
| S1 foundation | Opus | `reasoner/schema.py`, `reasoner/prompts.py`, `reasoner/client.py` (fake emits `ask` for alcohol/caffeine, and parses answers under fake), `config.py`, `models.py`, `db.py`, `tests/test_schema_ask.py`, `tests/test_db_questions.py` |
| S5 phone | Sol | `ios/QuestionListener.swift` (new), `ios/MacLink.swift` (handle `ask`, send `answer`), `ios/INTEGRATION.md` §8 |
| S2 manager | Sol | `actions/questions.py` (new: `AskLimiter`, `QuestionManager`), `actions/handlers.py` (`ask` branch), `reasoner/reasoner.py` (answer wake-up path, follow-up cap), `api/wiring.py` (construct + expire on tick + stats), `tests/test_questions.py` |
| S3 transport | Opus | `src/longevity/wire.py`, `src/longevity/server/ingest.py` (`answer` → `link.on_answer` callback), `capture/bridge.py` + `capture/speak.py` (ask message after the speak), `api/routes.py` (`GET /api/questions`, `POST /api/answer`), `tools/fake_phone.py` (`--answer "two"` replies to the first `ask`), `tests/test_ingest_answer.py`, `backend/tests/test_api_questions.py` |

Interface between S2 and S3 (fixed here):

```python
class QuestionManager:
    def __init__(self, db, speech: SpeechLimiter, timings: Timings, send_ask: Callable[[PendingQuestion], None], parser: AnswerParser): ...
    def ask(self, decision_id, t, episode_id, action: AskAction, followup_of=None) -> tuple[PendingQuestion | None, str | None]  # (row, suppressed_reason)
    def on_answer(self, question_id: str, text: str, heard: bool, t: float) -> None   # sync, non-blocking: schedules parse on the loop
    def expire(self, now: float) -> int
    def stats(self) -> dict   # {"open": 0|1, "asked", "answered", "expired", "suppressed", "last_ask_t"}
```

`send_ask` is provided by S3 (bridge) and does two things in order: the normal
speak of `question.question`, then `link.send_text(wire.ask_message(...))`.
`link.on_answer: Callable[[str, str, bool, float], None] | None` is set by the
bridge to `pipeline.questions.on_answer`.

## 7. Verification

- Both suites green (backend ≥ 1050, capture ≥ 75) plus the new tests.
- Fake path, no keys: `--source glasses --vlm fake --reasoner fake` +
  `tools/fake_phone.py --answer "yeah two"` with `T0_FAKE_FIELDS` making the fake tagger report alcohol → the feed shows
  `alcohol_seen → ask`, then `answer:q_… → count 2`, and `GET /api/questions` shows the row answered with `parsed.count == 2`; `GET /api/episodes` shows the episode's `reported` projection.
- Guard tests, one guard per test with the others satisfied: `one_open`, `same_episode`, `ask_min_gap`, `ask_max_per_hour`, `speech_gap`, `ask_unsupported`, `no_transport`.
- Expiry test: no answer → `expired`, annotate line says so, no episode change.
- Swift: `swiftc -typecheck` for iOS 17; device behaviour verified when the glasses are back.


## 8. Decisions after Astra's review (these override anything above)

### 8.1 Admission: the answer parse uses the single T1 slot
`QuestionManager.on_answer` is synchronous. It (a) atomically claims the row with
`db.claim_answer` (exactly once; a duplicate or late answer is logged and
ignored), then (b) calls `reasoner.try_answer(question, transcript, t)`, which
acquires the same non-blocking slot as `try_escalate`. If the slot is busy the
row is finalised as `answered` with `parsed = {"understood": false, "note":
"reasoner busy"}` and a decision row `answer:<qid>` is written with
`dropped=True, drop_reason="t1_busy"`. Nothing is queued or retried. The parse
call has its own deadline (`t1_deadline_s`, 15 s) and releases the slot in
`finally`. Under `--reasoner fake` the parser is a regex and still goes through
the slot so the code path is identical.

### 8.2 Transport: one coroutine, in order, bounded
`LongevityCapture.send_question(q) -> Awaitable[bool]` does, in one coroutine:
synthesise (ElevenLabs, 8 s timeout, text fallback), send the `audio`/`speak`
message, then send `wire.ask_message`. The manager runs it as one task, records
`sent_t` (Mac clock) on success and finalises the row `suppressed` with reason
`send_failed` on failure. While a question is open, `ActionHandler` drops any
`speak` (logged as `speak_dropped_listening`) so nothing talks over the answer
window. With `--source sim` (no phone) the send prints to the console and
returns True, so the Mac path is testable without hardware.

### 8.3 Answers live on the question, never on the episode
`EpisodeBuilder` recomputes `dominant` and `INSERT OR REPLACE`s the episode on
every tick, so a DB patch would be erased and would also conflate what the
camera saw with what the wearer said. Therefore: the transcript and the parsed
fields are stored only on `pending_questions`. The API projects them:
`GET /api/episodes` rows gain `reported: {confirmed, count, food_type, note,
question_id, answered_t} | null`, computed in the route from the latest
`answered` question for that episode. The dashboard and the healthspan adapter
read `reported`; "wearer reported 2" stays distinguishable from "2 seen".

### 8.4 Lifecycle and clocks
Statuses: `open` (sent or sending) → `answered` | `expired` | `suppressed`.
Only `open` rows can be claimed or expired; both transitions are single UPDATEs
guarded by `status='open'`, so an answer racing an expiry resolves to whichever
committed first and the other is a no-op. `expires_t = sent_t + ask_expire_s`
where `sent_t` is the Mac clock when the ask message went out (not `esc.t`);
`ask_expire_s` default **25** (synthesis ≤ 8, playback ~5, listen 8, slack).
`QuestionManager.expire(now)` runs from the downstream tick consumer *and* from
a 5 s asyncio timer owned by the manager, so expiry works when ticks stop. All
`t` values stored are Mac receipt time mapped through `Pipeline.clock`; the
phone's `t` on an answer is metadata only.

### 8.5 Follow-ups
Exactly one root question per episode, plus at most one direct child. The child
is produced by `AnswerParse.followup` (a string) only when
`parsed.understood and root.answer_kind == "yes_no" and parsed.confirmed`; the
manager converts it to `AskAction(text=followup, answer_kind="count",
fills="count")` for sighting episodes and `("free", "note")` otherwise. The
child is attempted immediately: it skips `ask_min_gap` and the same-episode
rule (that rule counts roots only) but must pass one-open, the hourly cap and
`SpeechLimiter.allow`; if denied it is dropped and recorded `suppressed`. A
child never has a child.

### 8.6 Guard order and accounting (`QuestionManager.ask`)
Evaluate in this order, stop at the first failure, and record the reason:
`ask_unsupported` (connected phone did not advertise `ask` in its hello; sim
counts as supported) → `no_transport` (glasses source, no phone connected) →
`one_open` → `same_episode` (roots only) → `ask_min_gap` (roots only) →
`ask_max_per_hour` → `speech_gap` (`SpeechLimiter.allow(t)`, the one mutating
check, called last and exactly once). A response containing both `speak` and
`ask`: the ask wins and the speak is dropped with `speak_dropped_for_ask`. A
send failure after `allow` forfeits that speech slot; accepted and documented.

### 8.7 Capability negotiation
The phone's `hello` gains `"caps": ["ask"]`. `GlassesLink` stores caps per
connection and exposes `supports(cap) -> bool` for the active socket. The Mac
never speaks a question to a phone that cannot open the mic.

### 8.8 Answer semantics (enforced in `AnswerParse` validation)
- `confirmed`: the wearer says the sighted item is theirs and they are having it. Ownership without consumption is `confirmed=false` with a note.
- `count`: servings of that item **today**, finite, `0 ≤ count ≤ 20`.
- `food_type`: must be in `models.FOOD_TYPES`, else null.
- `note`: ≤ 80 chars, what the wearer said in effect.
- `fills` names the primary field the question was for; the parser may set any other field it clearly heard ("yeah, two" sets both). `heard=false` or an empty transcript → row `expired` with note `nothing heard`, no fields. `understood=false` → note only, no semantic fields.

### 8.9 Reporting
The `ask` action dict stored in `decision.actions` gains `question_id` and
`outcome` (`sent` | `suppressed:<reason>`); the decision row is re-inserted after
the handler runs, as it already is for `spoke`. An answer writes a decision row
with `trigger="answer:<qid>"`, `episode_id`, `interpretation=parsed.note`, and
one `annotate` action (`"wearer: <note>"`), or a `dropped` row per §8.1.
Expiry appends an annotate line `"asked: <question> — no answer"`. Dashboard
rendering is out of scope for this branch; `docs/API.md` documents the shapes
(`GET /api/questions`, `reported` on episodes, the new action fields).

### 8.10 Fake tagger override
`longevity.vlm.FakeClient` reads `T0_FAKE_FIELDS` (JSON) to override its
constant fields, so a key-less run can produce `alcohol_visible=true` and drive
the whole loop through the gate.

### 8.11 Interfaces (final)
```python
# S1 — reasoner/schema.py, reasoner/client.py
class AnswerParser(Protocol):
    async def parse(self, question: PendingQuestion, transcript: str, episode: Episode | None) -> AnswerParse: ...
def make_answer_parser(settings: Settings, mode: Literal["openai", "fake"]) -> AnswerParser
ANSWER_TEXT_FORMAT: dict   # strict Responses schema for AnswerParse

# S2 — actions/questions.py
class QuestionManager:
    def __init__(self, db, speech, timings, *, send: Callable[[PendingQuestion], Awaitable[bool]], supports_ask: Callable[[], bool], has_transport: Callable[[], bool]): ...
    def ask(self, *, decision_id, t, episode_id, action: AskAction, followup_of: PendingQuestion | None = None) -> tuple[PendingQuestion | None, str | None]
    def on_answer(self, question_id: str, text: str, heard: bool, t: float) -> None  # sync; claims, then reasoner.try_answer
    def expire(self, now: float) -> int
    def start(self) / stop(self)          # the 5 s expiry timer
    def listening(self) -> bool
    def stats(self) -> dict
    reasoner: Reasoner                    # set by wiring after both exist
# S2 — reasoner/reasoner.py
Reasoner.try_answer(question, transcript, t) -> bool   # slot-guarded; on success schedules _answer_run
ActionHandler.apply(decision_id, t, resp, *, episode_id: str | None = None)

# S3 — capture/bridge.py, server/ingest.py, api/routes.py
LongevityCapture.send_question(q: PendingQuestion) -> bool   # async
GlassesLink.on_answer: Callable[[str, str, bool, float], None] | None
GlassesLink.supports(cap: str) -> bool
GET /api/questions?limit=20 ; POST /api/answer {question_id?, text, heard=true} ; POST /api/ask {text, answer_kind, fills, episode_id?}
```
Wiring (S2, `api/wiring.py`): construct the manager with
`send=capture.send_question` (or the console sender for sim),
`supports_ask=lambda: capture.link.supports("ask")`, `has_transport=lambda:
bool(capture.link.clients)`; set `capture.link.on_answer =
questions.on_answer`; set `questions.reasoner = reasoner`; pass `questions` to
`ActionHandler`; call `questions.expire(tick.t)` in `downstream()`; expose
`questions.stats()` in `status()["questions"]`.
