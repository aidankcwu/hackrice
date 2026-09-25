# PERSONA_PHILOSOPHY.md — what Bryan says, and what he should be

**Who this is for.** Someone starting from the code with no history on this project,
tasked with changing how the system decides to speak. Part 1 describes what it does
today and where that behaviour comes from, with file pointers so every claim can be
checked. Part 2 is the design position: what the experience should feel like instead.

Part 1 is observation and code reading. Part 2 is a judgement about the product, argued
from a real session. Treat Part 1 as fact to verify and Part 2 as a brief to work from.

**Context you need first.** [CLAUDE.md](../CLAUDE.md) for the architecture in one line
and the invariants. [SPEC.md](../SPEC.md) §2–§3 for the tick producer and trigger gate
(the two sections its own header marks "thought through, authoritative"). The audience
for the demo this is written against is a single investor wearing the glasses for
ten to fifteen minutes, poking at the phone app while he does it.

---

# Part 1 — How it behaves today

## 1.1 What it can perceive at all

Everything the system knows about the world arrives as one flat record per frame, every
1.5 seconds, from a vision model. The field set is defined in exactly one place:
[`src/longevity/ai_fields.py`](../src/longevity/ai_fields.py).

It is an inventory of what is visible:

- **Booleans** — `food_present`, `caffeine_visible`, `alcohol_visible`, `screen_present`,
  `vegetation_visible`, `people_present`, `people_interacting`,
  `direct_sunlight_visible`, `outdoor_visible`, `smoking_or_vaping_visible`,
  `medication_visible`, and the tri-state `phone_in_hand`
- **Enums** — `scene`, `activity`, `food_type`, `drink`, `people_count`
- Plus a free-text `caption` and an `objects` list

Read that list carefully, because it is the ceiling on everything downstream. **There is
no field for what the wearer is doing over time, no field for intent, no field for
change or novelty, and no field for whether anything is different from a minute ago.**
Each record is a snapshot of objects in a scene, independent of every other record.

`activity` is the only field that gestures at behaviour, and it is a single enum value
inferred from one frame.

## 1.2 What makes it speak

Ticks accumulate and a gate decides when to escalate to the reasoner, which may then
speak. The triggers live in
[`backend/pipeline/gate/triggers.py`](../backend/pipeline/gate/triggers.py) and are
named for what they watch: `screen_sustained`, `people_sustained`, `outdoor_sustained`,
`food_in_frame`, `caffeine_sighting`, `alcohol_sighting`, `medication_seen`,
`stillness`, `biometric_anomaly`, plus `change` and `cue`.

Almost every one is of the form *"this flag was true N times in the last W seconds."*
That is the shape of the whole gate: presence, counted.

## 1.3 The timing profile the demo actually runs

[`backend/pipeline/config.py`](../backend/pipeline/config.py) defines two profiles,
`Timings.production` and `Timings.demo`, selected by `demo_mode` (see the factory around
line 616). **The hosted tester backend runs `Timings.demo`** — `/api/status` reports
`demo_mode: true`.

The two differ by more than a little:

| | `production` | `demo` |
|---|---|---|
| `screen_sustained` | 20 hits / 60 s | **3 hits / 20 s** |
| `people_sustained` | 15 hits / 60 s | 2 hits / 20 s |
| `outdoor_sustained` | 15 hits / 60 s | 2 hits / 20 s |
| `food_min_hits` | 3 | **1** |
| `global_escalation_min_gap` | 60 s | **2 s** |
| `change_cooldown_s` | 20 s | 8 s |
| `trigger_cooldown_default` | 1200 s | — |
| `speech_min_gap` | **600 s** | **0.0** |
| `speech_max_per_hour` | — | **120** |
| `ask_min_gap` | — | 0.0 |
| `conversation_cooldown_s` | — | 0.0 |

At a 1.5 s tick, `3 hits / 20 s` means **roughly four and a half seconds of a screen
being visible** is enough to arm the screen trigger. `speech_min_gap: 0.0` means there
is no floor at all on how quickly one spoken line can follow another. `food_min_hits: 1`
means a single frame containing food arms the food trigger.

These numbers are not accidental and they are not a bug — they were tuned so that a demo
produces audible output quickly. The consequences below follow directly from them.

## 1.4 What that produces: an observed session

A real five-minute session, reported by the wearer:

**What actually happened.** He sat at his desk with VS Code open on a laptop. His phone
lay beside the laptop, and he picked it up periodically because he was looking at this
project's own phone app. A bottle of sparkling water sat on the desk, permanently in
frame. Partway through, he got up, walked to a different desk, picked up a tub of
creatine, and looked at it.

**What the system said.**

- "Put the phone down" — **twice**
- "Phone and laptop again"
- "Put the phone down and take a sip of the water"
- Nothing at all about the creatine

Four observations about that transcript:

**It described objects, not activity.** "Phone and laptop again" is a list of what was in
frame. It is not a thought about what he was doing. He was working; the system saw two
devices.

**It missed the only intentional act in five minutes.** Getting up, crossing the room,
picking up a specific object and examining it is the one moment with visible intent. It
went unremarked, while the furniture got commentary. Nothing in §1.1 represents "the
wearer deliberately went and looked at something," so nothing downstream could act on it.

**It treated presence as opportunity.** The water was mentioned because water was
visible and water is healthy. He had drunk from it minutes earlier. The bottle never
leaves the desk, so on current logic it is a standing reason to speak, indefinitely.
This generalises: **anything permanently in the wearer's environment becomes a permanent
prompt**, which is backwards, since a thing that is always there carries the least
information.

**It gave instructions rather than observations.** "Put the phone down" is a command. It
assumes an authority the system has not earned and tells the wearer nothing he did not
already know.

## 1.5 Repetition

Saying "put the phone down" twice in five minutes is the clearest signal to a wearer
that nothing is actually listening.

The intent is already written down. The persona in
[`backend/pipeline/reasoner/prompts.py`](../backend/pipeline/reasoner/prompts.py) says,
in as many words, that the wearer *"hates being nagged and will ignore the system if it
repeats itself, so say a thing once, at the moment it is"* relevant. The prompt is not
the problem.

What is missing is enforcement. Per-trigger cooldowns exist
(`trigger_cooldown_default`, `MEDICATION_COOLDOWN_S`, and friends in `triggers.py`), but
in the demo profile most of the relevant gaps are at or near zero, and — more
fundamentally — **a cooldown is a timer on a trigger, not a memory of what was said.**
Nothing compares a candidate line against the lines already spoken this session. Two
different triggers, or the same trigger after its cooldown, can produce the same sentence
about the same object.

## 1.6 When it stays silent

Silence today is a side effect rather than a decision. It happens when:

- no trigger's hit count is met inside its window
- a trigger fired recently and is inside its cooldown
- the single T1 slot is busy (`t1_max_concurrent: 1`) and the escalation is dropped
- a conversation is already open, or the mouth is busy playing a previous clip
- the vision call overran its budget and the tick carries no `ai` block at all
  (invariant 3 in [CLAUDE.md](../CLAUDE.md) — gaps are tolerated by design)

Note what is *not* in that list: **"there was nothing worth saying."** Silence is never
chosen on the merits. The system does not have a concept of declining to speak because
the moment did not deserve it.

## 1.7 The summary of Part 1

The system narrates frames. Per 1.5 seconds it receives an inventory of visible objects,
counts how often flags are true, and when a count crosses a threshold it produces a line
about the object that crossed it. Under the demo profile the thresholds are low and the
gaps between lines are near zero.

Nothing in the pipeline models what the wearer is doing, why, or what has changed. It is
an object detector with opinions and a short timer, and from the inside of the glasses it
sounds like one.

---

# Part 2 — What it should be instead

## 2.1 The target

The wearer should come away believing **it understood his day** — not that it can talk.

The current tuning optimises for *something happens quickly*. Fast but shallow reads as
stupid. One uncannily right line beats nine reasonable ones, and every line a thoughtful
person would not have said costs more credibility than the good lines buy.

## 2.2 Seeing is not understanding

"Phone and laptop again" is the whole diagnosis in four words. A person standing behind
the wearer does not see a phone and a laptop. They see *someone working, with their phone
next to them*. Identical pixels, entirely different object of thought. One is inventory;
the other is a situation.

The unit of understanding is not the frame. It is the stretch of time in which the wearer
was doing one thing.

## 2.3 The creatine moment is the one worth catching

Getting up, crossing the room, picking up a tub and examining it is the only act in five
minutes with intent behind it. A person in the room registers that immediately — a
pattern broke, movement had purpose, something was deliberately inspected. They might say
"thinking about taking that?" or nothing at all, but they noticed.

**Attention should follow intent, not inventory.** What is worth speaking about is where
the wearer's own attention went, not what happened to be in shot.

## 2.4 Presence is not opportunity

Water visible → water is healthy → recommend water. That logic makes it a vending machine
with opinions.

A person mentions water if you have not had any for hours, or you just came in from a
run, or you look wrung out. Never because a bottle is sitting there.

The general rule: **the constant features of an environment are the least informative
things in it.** The desk, the laptop, the bottle that never moves — these should decay
toward silence. What deserves attention is what changed.

## 2.5 What "another person in the room" actually means

Four behaviours, and they are the whole specification:

**They are mostly silent.** Silence is the resting state of someone who understands you.
Speaking is the exception that needs a reason. This is the single largest gap between the
current system and the target.

**They react to change, not to state.** Nobody remarks that you are still at your desk.
They remark when you get up.

**They speak to intent, not to objects.** They carry a running theory of what you are
doing and why, and everything they say is aimed at that theory. Being occasionally wrong
is forgivable; being irrelevant is not.

**They remember what they just said.** Repeating yourself inside five minutes is what
tells someone nobody is home.

## 2.6 Being noticed beats being helped

This is the principle worth building the demo around.

Advice is cheap and faintly insulting. Noticing is rare and lands.

*"Put the phone down"* asserts authority it has not earned and conveys nothing new.
*"You've been heads-down since nine"* earns its place: it proves continuity of attention
and leaves the conclusion to the wearer. The first makes someone take the glasses off.
The second makes them wonder what else it noticed.

Help implies the system thinks you are doing it wrong. Noticing implies it was paying
attention. Only one of those is the product.

**Corollary for tone:** prefer the observation to the instruction, almost always. If an
instruction is genuinely warranted, it should read as the conclusion of an observation
the wearer already agrees with.

## 2.7 Tiering: observation, speech, question

Chattiness and over-strictness are not two problems. They are one, and the fix is not
"speak less" — that yields the production profile's near-silence, which demos as a dead
app. The fix is to separate cheap acts from expensive ones:

**Noticing** should be frequent, ambient and costless. It belongs in the log, the day's
record, the live view — not in the wearer's ear. Someone scrolling the phone and seeing
*"3:42 Meal, rice bowl"* is better proof of perception than any spoken line.

**Speaking** should be rare and earned, and should clear a bar the wearer would endorse
in hindsight.

**Asking** is the most expensive act, because it demands a reply. It should be rarest of
all, and is the most likely to delight when it lands.

Today everything routes to the top tier.

## 2.8 The specific case: a glance is not a session

The screen trigger measures presence, so it cannot distinguish answering a text from
forty minutes of scrolling. Three ideas, stated at the level of behaviour rather than
implementation:

**Duration, with hysteresis.** A behaviour should have to persist well past the length of
an ordinary interruption before it counts, and a brief look away should not reset it.

**Context over object.** A second screen while already working at a screen is not a new
behaviour; it is the same behaviour. The informative signal is a screen *after* wind-down,
or a screen where daylight should have been — not a screen during work.

**Escalating specificity, never repetition.** First occurrence: an observation. If the
behaviour genuinely continues: something more specific that refers back to the first.
Third: nothing. Knowing when to stop is the part a wearer remembers.

## 2.9 Silence has to be visible

The catch in making the voice rare: if it says nothing, the wearer may assume it is
broken.

So the sense of presence has to come from somewhere other than speech — the live view of
what it is seeing, the log filling in, the day assembling itself on the phone. He should
be able to *look* and see that it was awake the whole time.

That is what frees the voice to be rare. **Presence is continuous; speech is occasional.**
Today they are fused, so the only way the system can demonstrate it is alive is by
talking, and so it talks.

## 2.10 The arc of a good session

**Show that it sees.** Thirty seconds, his own view, on the phone. No words needed.

**Then get out of the way.** The middle of the demo is the stretch where nobody looks at
the phone and he forgets it is there. That forgetting is load-bearing — you cannot be
surprised by something you are monitoring.

**One line, well-timed, that makes him turn his head.** Not advice. An observation only
something genuinely watching could have made.

**Close on the day it built.** The payoff is retrospective: it was paying attention the
whole time he forgot about it. The contrast between how little it said and how much it
noticed is the product.

## 2.11 The test to apply to every line

Before anything is spoken:

> **Would a thoughtful person in the room have said this out loud, right now?**

"Take a sip of water" because a bottle is visible — no person says that. "Phone and
laptop again" — no person says that. "Second time you've been over to look at that" — a
person absolutely says that.

That single question rejects almost everything said in the observed session and keeps the
one thing that was worth saying.

---

## Where to look

| Concern | File |
|---|---|
| What the system can perceive at all | [`src/longevity/ai_fields.py`](../src/longevity/ai_fields.py) |
| What arms a trigger | [`backend/pipeline/gate/triggers.py`](../backend/pipeline/gate/triggers.py) |
| Thresholds, cooldowns, speech gaps, both profiles | [`backend/pipeline/config.py`](../backend/pipeline/config.py) |
| Persona, tone, and the "say it once" instruction | [`backend/pipeline/reasoner/prompts.py`](../backend/pipeline/reasoner/prompts.py) |
| Decision to escalate | [`backend/pipeline/gate/gate.py`](../backend/pipeline/gate/gate.py) |
| Turning a decision into speech | [`backend/pipeline/reasoner/reasoner.py`](../backend/pipeline/reasoner/reasoner.py) |
| Spoken conversation and the ask flow | [`backend/pipeline/conversation/agent.py`](../backend/pipeline/conversation/agent.py) |
| Architecture, invariants, what is known thin | [`CLAUDE.md`](../CLAUDE.md) |
| Tick schema and the authoritative §2/§3 | [`SPEC.md`](../SPEC.md) |
| What the phone app shows, and what a tester does | [`docs/APP_NATIVE_NOTES.md`](APP_NATIVE_NOTES.md), [`TESTFLIGHT.md`](../TESTFLIGHT.md) |

## Two cautions

**The field set is the ceiling.** No amount of prompt or threshold work will produce
intent-aware behaviour while perception is a per-frame object inventory (§1.1). If the
conclusion is that the system needs a representation of activity over time, that is a
change in `ai_fields.py` and everything reading it — and per
[CLAUDE.md](../CLAUDE.md), §9 fields live in exactly one place and nothing else may name
one.

**Tuning cannot be validated from a desk.** Every threshold in §1.3 is a number that can
be changed in a minute, and no reading of the code will tell you whether a given setting
feels right. The only instrument is wearing it for an hour and counting the lines you
wanted against the lines you got. That count is the design.
