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
gate wakes T1 (e.g. alcohol_sighting)
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
`expire_questions(now) -> int`, and `update_episode_dominant(episode_id, patch: dict)`.

Episode write-back: `episode.dominant` gains `confirmed` (bool), `count`
(number), `food_type` (str), `answer` (the note), `answered_t`. Consumers such
as the healthspan adapter read `dominant.get("count")` before clustering
sightings.

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
  `tools/fake_phone.py --profile drinking --answer "yeah two"` → the feed shows
  `alcohol_sighting → ask`, then `answer:q_… → count 2`, and
  `GET /api/episodes` shows the sighting with `dominant.count == 2`.
- Guard test: two sightings 5 s apart → one ask, one suppressed with reason `ask_min_gap`.
- Expiry test: no answer → `expired`, annotate line says so, no episode change.
- Swift: `swiftc -typecheck` for iOS 17; device behaviour verified when the glasses are back.
