# Conversation agent — the third agent, the one that talks

Three agents. **T0 (Gemini)** tags frames. **T1 (the clerk, GPT)** is woken by the
gate and does everything silent: annotate, log_insight, watch, remember, episodes.
**The voice agent (GPT)** owns the mouth. It holds **one conversation at a time**; the
clerk cannot speak or ask directly any more, it can only *hand off* a topic.

## 1. Hand-off (clerk -> voice agent)

The clerk's `speak` and `ask` actions become hand-off requests. Their `text` is a
**topic and reason in plain words** ("picked up a wine glass, ownership unknown"),
not the sentence to say. The voice agent writes the words.

- `speak` -> hand-off with `mode: "statement"` preferred (one line, no mic).
- `ask`   -> hand-off with `mode: "question"` preferred.
The voice agent may override the mode (it can answer a `speak` with a question or
an `ask` with a statement) because it is the one holding the thread.

If a conversation is **active**, the hand-off is dropped and the decision records
`outcome: "conversation_active"` (like `speak_dropped`). Keyword triggers (rice
krispy) and biometric triggers go through the same path.

A short **cooldown** after a conversation closes (`conversation_cooldown_s`, demo
10 s) drops hand-offs with `outcome: "conversation_cooldown"`.

## 2. Lifecycle

```
opened  --(statement)-->  closed
opened  --(question)-->  listening --(answer)--> replying --(statement)--> closed
                                                       \--(question, if < max)--> listening
        listening --(nothing heard / expiry)--> closing line or silence --> closed
        any state --(lifetime exceeded)--> closed
```

Limits (Timings): `conversation_max_questions` (demo 2), `conversation_lifetime_s`
(demo 60), answer wait = existing `ask_expire_s` (demo 25).

## 3. Context the voice agent gets

**Opening turn (system + user):**
- system: the persona (DB override else default) + learned lines + a short voice-agent
  objective (below).
- user: the hand-off topic and reason; today's memory lines (last ~15); one line per
  **closed conversation today** (`22:41 asked about the cereal -> "yes, mine"`), so a
  settled topic is not reopened; the tick table for the last ~20 s; the current frame
  and up to 2 earlier frames (scene-change picked, same selector as the clerk).

**Reply turn (user):** the transcript (`heard: true/false`), the ticks since the
question was sent, and up to 3 frames since then. Nothing else is repeated; the
thread already holds the earlier turns. The thread is the Responses API message
list kept in memory for the life of the conversation and discarded at close.

**Voice-agent objective (fixed):**
- One line at a time, in the persona's voice, spoken aloud. Short.
- A question opens the mic; a statement ends the conversation. Prefer to end.
- Never force a follow-up: "yes, it's water" -> "ok good" and done.
- If the transcript reads like noise or UI words ("Play", "Show", fragments), treat it
  as not heard: close with a short line or silence, do not repeat the question.
- Never reopen a topic listed as settled today.
- Return the settled facts so the clerk can score them.

## 4. Structured reply (strict JSON)

```json
{
  "utterance": "Is that wine yours?",          // "" means stay silent
  "kind": "question" | "statement",
  "settled": {"confirmed": true|false|null, "count": 2|null,
              "food_type": "fruit"|null, "note": "just water"|null},
  "heard": true|false,                          // reply turns only: did the transcript read as an answer
  "done": true|false                            // true closes the conversation after this utterance
}
```
`kind: "statement"` implies `done: true`. `kind: "question"` with the question cap
reached is coerced to a statement by code.

## 5. Transport (unchanged phone protocol)

A question goes out as today's `ask` wire message with a `question_id`, through
`QuestionManager.ask(...)` with `conversation_id` set; the phone listens exactly
as now and returns `answer`. `QuestionManager.on_answer` routes an answer whose
question belongs to an active conversation to the voice agent instead of the
answer parser. Statements go out through the existing `speak()` (ElevenLabs).
**No iOS changes.**

## 6. Write-back on close

- One memory line for the clerk: `22:41 asked about the wine -> "it's water"` (or
  `-> nothing heard`), so later wake-ups know.
- `settled` fields applied to the hand-off's episode exactly as an answered
  question is today (`reported` projection, episode label suffix). The old answer
  parser is bypassed for conversation questions.
- The conversation row is persisted (§7) and the questions table rows keep their
  status so the existing questions panel still works.

## 7. Storage and API

Table `conversations`: `id, opened_t, closed_t, reason, topic, decision_id,
episode_id, state ("active"|"closed"), turns JSON, settled JSON, close_reason`.
`turns` is a list of `{t, role: "agent"|"wearer", text, kind?, heard?}`.

- `GET /api/conversations?limit=20` -> rows newest first (turns included).
- `GET /api/conversations/{id}` -> one row.
- `GET /api/conversation/current` -> the active one or `null`.
- `POST /api/conversation/open` `{topic, mode?}` -> opens one by hand (demo); 409
  `{reason: "conversation_active"}` if one is running.
- Decisions: `speak`/`ask` actions gain `outcome`: `"handed_off:<conversation_id>"`,
  `"conversation_active"`, `"conversation_cooldown"`, `"no_transport"`.

## 8. Dashboard

`ConversationsPanel` replaces the questions list in the drawer (the questions
panel stays available for the raw rows): one card per conversation, a thread of
lines (agent left, wearer right), the settled chips, the close reason, and a
pulsing ACTIVE badge on the running one. A one-line "Say something about…" box
posts `/api/conversation/open`.
