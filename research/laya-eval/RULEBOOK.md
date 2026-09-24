# Labeling rulebook for the decider

Written 2026-09-24, before any labels exist. This is how every state (real or synthetic)
is labeled for the 10 decider questions. A labeler applies the rules in order and stops
at the first one that decides the answer. When no rule fires, the **default** holds.

The state format is in `SCHEMA.md`. The questions are the exact ones the decider asks
(`backend/pipeline/reasoner/decider.py`, mirrored in `scripts/questions.py`).

## 0. Shared definitions

Every rule below uses these terms. Work them out once per state, before labeling.

| Term | Definition |
|---|---|
| **fresh tick** | A `recent` entry without `no_ai`. `recent` is newest first; `recent[0]` is now. |
| **newest view** | The first fresh tick in `recent`, provided its `age_s` <= 5. If there is none, the state is **blind**. |
| **key object** | What the trigger is about: the item named in `trigger.reason` if any, else the item in hand or newly appeared in the newest view (caption, `objects`, `hot`), else the trigger's own subject (see the topic table in §9). |
| **in hand / imminent intake** | The newest view shows the key object about to be consumed or being consumed: `activity` is `eating` or `drinking`, or the caption or reason says holding, opening, lifting, pouring, sipping, biting, lighting, or "about to". A drink sitting on the desk is **not** imminent. |
| **treat** | Candy, dessert, sweets, baked goods, chips, soda, fast food, fried food, pizza, burger (the `UNHEALTHY_FOOD_TYPES` family in `ai_fields.py`). |
| **late caffeine** | Coffee, tea, energy drink, soda, boba or anything `caffeine_visible`, when `clock.local` is at or after the persona's stated cutoff, or 14:00 if the persona states none, and before 05:00. |
| **late night** | `clock.local` between 23:00 and 04:59, or after the persona's stated bedtime or wind-down. |
| **recent line** | One of the last 6 lines of `today`. `today` lines carry no timestamps; the decider writes roughly one line per escalation, so the last 6 lines stand in for "the last ~10 minutes". |
| **said on topic** | A recent line starting `said: "` whose words are about the same topic (§9) as this moment. |
| **asked about it** | A line **anywhere** in `today` starting `asked` about the key object, answered (`-> "..."`) or not (`— no answer`, `— nothing heard`). |
| **complied** | A line after the matching `said:` line, or the newest view, shows the wearer did what was said (`said: "Phone down..."` then `Phone on the desk...`; `said: "water first"` then `Drank water...`). |
| **material change** | Compared with the last recent line on the same topic: a different item (water to beer), a larger count (second coffee), a clock threshold crossed (caffeine cutoff, bedtime), or intake starting where before the item was only visible. A paraphrase of the same scene is not a material change. |
| **conversation** | The newest view has `people_interacting` in `true` or `activity` = `talking`, or an open `conversation` episode. |

## 1. Speak

*"Should the companion say something in the wearer's ear right now?"* Silence is the
default. Speak only when a short remark right now can change the wearer's **next** choice.

**Default: no.** Target yes-rate across all states: about 10-15%.

| # | Rule (first that fires wins) | Answer |
|---|---|---|
| S1 | **Blind.** The state is blind (no fresh tick within 5 s). | no |
| S2 | **Never repeat.** There is a *said on topic* line and no *material change* since it. | no |
| S3 | **Already complied.** The wearer complied with the last line said on this topic. | no |
| S4 | **Ambiguous key object that matters.** Rule K2 (ask) or L1 (look) fires and the answer would change what to say (is it beer or water?). Ask or look first. | no |
| S5 | **Imminent intake of a risk item:** in hand and it is a treat, alcohol, late caffeine, nicotine/vape (`smoking_or_vaping_visible`), or a food the persona says to cut. Applies during conversation too. | **yes**, urgency `now` |
| S6 | **Persona pattern forming:** the persona names a trigger ("a second time in a day", "past the cutoff", "near a run or bedtime") and `today` plus this moment meet it, even if the item is not yet in hand. | **yes**, urgency `soon` |
| S7 | **Conversation in progress** and S5 did not fire. Speaking would talk over someone. | no |
| S8 | **Late-night screen**: `screen_block` open >= 60 min during late night, and the persona does not call late screens its baseline. If it does (e.g. "screen time and late nights are his baseline"), only speak when the screen block collides with a stated goal (a run in the morning, sleep debt in `trends`). | **yes**, urgency `soon` |
| S9 | **Long stillness in daytime:** trigger `stillness`, or a sitting or screen block >= 90 min between 09:00 and 18:00, with low steps or daylight in `trends`, and no movement nudge said today. | **yes**, urgency `soon` |
| S10 | Healthy or neutral intake (water, salad, fruit), ordinary work, posture, scenery, medication sitting on a desk, outdoor time already under way. | no |

Examples:
- 21:40, `recent[0]` caption "hand lifting a can of beer", persona wants less alcohol, no alcohol line today → **S5 yes, now**.
- Same, but `today` ends `said: "That's the second beer tonight."` and the newest view is the same can → **S2 no**.
- 02:08, `today` ends `said: "Phone down, water bottle first."` then `Opened and drank water at the laptop.` → **S3 no**.
- 15:30, "person holding an iced coffee", persona cutoff "none after 2 p.m." → **S5 yes, now**. At 09:00 the same frame → **S10 no**.

## 2. Ask

*"Should the companion ask the wearer a short question right now?"* Only when the wearer
alone can settle an ambiguity that changes what happens next.

**Default: no.** Target yes-rate: about 5%.

| # | Rule | Answer |
|---|---|---|
| K1 | **Already asked.** An *asked about it* line exists for this key object today (answered or not), or a *said on topic* line is recent without material change. | no |
| K2 | **Only the wearer knows, and it matters.** The topic is caffeine, alcohol, food or medication, and one of these is unknown and not in the text: whose it is (several drinks, a shared table), what it is (a bare "bottle", "cup", "glass", "can", "pill" whose type decides the verdict), or how many (the persona counts it). | **yes** |
| K3 | A camera frame could settle it (label, colour, logo): prefer `look` (L1) and answer ask **no**, unless the question is ownership or count, which a frame cannot settle. | no |
| K4 | **Conversation in progress.** Do not interrupt a conversation with a question. | no |
| K5 | Blind state. There is nothing specific to ask about. | no |

Ask and speak are rarely both yes. Both are yes only when S5 fires and the count is
unknown and the persona tracks it ("Is that your second drink tonight?" is a single question-shaped remark).

Examples:
- `trigger.reason` "desk with multiple bottles beside the laptop, drink type/ownership unclear", nothing asked today → **K2 yes**.
- Later, `today` has `asked about bottles on the desk ... -> "Has water bottles man"` → **K1 no**.

## 3. Annotate

*"Should this moment get a line in today's running summary?"*

**Default: no.** Target yes-rate: about 40-55%.

| # | Rule | Answer |
|---|---|---|
| N1 | `today` is empty. | yes |
| N2 | Blind state and the trigger reason adds nothing. | no |
| N3 | **Repeat.** One of the last 3 `today` lines already describes the same scene, activity and key object (paraphrases count as the same). | no |
| N4 | **New information**: a new object, activity, scene or episode kind; intake starting or finishing; a sighting; the wearer's reaction to a nudge; a threshold crossed. | yes |

Examples:
- Last line "Phone on the desk by the laptop while coding." and the newest view is "laptop with code on screen, phone beside it" → **N3 no**.
- Last line "Phone in hand at the laptop" and now "hands opening a water bottle" → **N4 yes**.

## 4. Log insight

*"Should this be logged as an insight in today's health report?"* Measurable health facts only.

**Default: no.** Target yes-rate: about 15-20%.

| # | Rule | Answer |
|---|---|---|
| I1 | Blind state. | no |
| I2 | **Already logged.** A `today` line already reports this same sighting, count or threshold for the same open episode. | no |
| I3 | **Countable intake event**: a caffeine, alcohol, nicotine, water or food intake actually happening (in hand or being consumed), or a meal finished. | yes |
| I4 | **First sighting** in its episode: `medication_sighting`, `caffeine_sighting`, `alcohol_sighting` with `minutes_open` < 1 and no `today` line about it. | yes |
| I5 | **Threshold crossed**: `screen_block` or sitting passing 60, 120 or 180 min; `outdoor_block` passing 10, 20 or 30 min, or any outdoor or daylight time before 10:00; a `conversation` passing 5 min. | yes |
| I6 | Ambient scenes, posture, an object merely visible, a repeat. | no |

Examples:
- `medication_seen` trigger, `medication_sighting` open 0.2 min, no medication line → **I4 yes**.
- The same bottle on the next escalation, `Medication bottle on the desk ...` already in `today` → **I2 no**.

## 5. Remember

*"Does this reveal something to remember about the wearer long term?"* Durable
preferences and habits, not moments.

**Default: no.** Target yes-rate: about 3-7%.

| # | Rule | Answer |
|---|---|---|
| R1 | The fact is already in `persona` or `trends`. | no |
| R2 | **An answer that reveals a preference or standing fact**: the last `asked ... -> "..."` line says something that will hold tomorrow ("oat milk always", "that's my roommate's", "I don't drink coffee"). | yes |
| R3 | **A habit visible today**: the third or later instance of the same behavior in `today` (third late coffee, phone picked up at the laptop every time), or a stated rule by the wearer. | yes |
| R4 | Anything else, including a single sighting. | no |

## 6. Watch

*"Should the companion check on this again later?"* Yes when the outcome is open and
a recheck in a few minutes could change what happens.

**Default: no.** Target yes-rate: about 20-30%.

| # | Rule | Answer |
|---|---|---|
| W1 | **Settled**: the item is finished or put away, the wearer complied, the scene was left. | no |
| W2 | **Open outcome**: a drink put down part-full; food present but not yet eaten; a risk item visible but not yet in hand; medication seen but not taken; a question asked with no answer yet; a nudge just said and compliance not yet seen. | yes |
| W3 | **Ongoing block that could cross a threshold**: `screen_block` during late night; an `outdoor_block` whose minutes will earn credit; a sitting block past 60 min in daytime. | yes |
| W4 | Blind state with an open trigger: recheck when the camera returns. | yes |

## 7. Act

*"Should the phone take a concrete action right now?"* Only the listed phone actions count.

**Default: no.** Target yes-rate: about 3-5%.

| # | Rule | Answer |
|---|---|---|
| C1 | The same action already appears in `today` (e.g. "added a walk", "shielded apps"). | no |
| C2 | **Calendar walk**: trigger `stillness` or a sitting or screen block >= 90 min, between 09:00 and 18:00, and `trends` or `today` show low daylight or steps below target. | yes |
| C3 | **App shielding at wind-down**: late night, `phone_use` or a `screen_block` open >= 30 min, and the persona does not call late screens its baseline or asks for help winding down. | yes |
| C4 | **Reminder the persona asks for** (e.g. "remind me about my evening dose") and the moment matches. | yes |
| C5 | Anything else. | no |

When `act` is yes, urgency is at least `soon`.

## 8. Look

*"Would a closer look at the camera frame change the decision?"*

**Default: no.** Target yes-rate: about 8-12%.

| # | Rule | Answer |
|---|---|---|
| L1 | **Generic or contradictory key object**: the key object is only named generically ("bottle", "cup", "can", "glass", "pill", "pack", "drink") and its type decides the verdict (water vs beer, coffee vs tea); or caption, `objects` and `true` disagree (caption says "beer", `alcohol_visible` absent; `activity` `drinking` with no drink in `objects`). | yes |
| L2 | **Newest tick has no AI** (`recent[0]` is `no_ai`) and the trigger is `cue` or `change` with an empty reason. | yes |
| L3 | The question is ownership or count, which a frame cannot answer. | no (ask instead) |
| L4 | Caption, objects and tags agree. | no |

Examples:
- Objects `["laptop", "keyboard", "bottle"]`, reason "a bottle sits on the desk, likely water" → **L1 yes**.
- Caption "Hands opening a Kirkland water bottle" → **L4 no**.

## 9. Topic

*"Which of the wearer's health topics is this moment about?"* Exactly one of:
`caffeine, food, alcohol, screen, people, outdoors, medication, sleep, movement, other`.

**T1. Named triggers map directly.**

| Trigger | Topic |
|---|---|
| `caffeine_seen` | caffeine |
| `alcohol_seen` | alcohol |
| `medication_seen` | medication |
| `food_in_frame` | food |
| `screen_sustained` | screen |
| `people_sustained` | people |
| `outdoor_sustained` | outdoors |
| `stillness` | movement |
| `biometric_anomaly` | movement when the newest view shows exertion, else other |

**T2. `cue`, `change` and keyword triggers:** the topic of the key object, by this
priority when several are present: alcohol > nicotine (→ other) > caffeine > medication >
food (includes water, juice and any non-caffeinated drink) > screen (laptop, phone) > people >
outdoors > movement > sleep > other.

**T3.** `sleep` is for lying down, sleeping, bed, or a moment whose subject is bedtime
itself. A late-night screen is still `screen`.

**T4.** A blind state with no reason: the topic of the open episode with the smallest
`minutes_open`, else `other`.

Examples: "water bottle over a laptop and phone", trigger `change` → **food**. Phone in
hand at the laptop, nothing else new → **screen**.

## 10. Urgency

*"How soon does the wearer need to hear about this?"* One of `can wait`, `soon`, `now`.
Derived from the answers above, so it is mechanical:

| # | Rule | Urgency |
|---|---|---|
| U1 | speak is yes under S5, or ask is yes about an item in hand | `now` |
| U2 | any of speak, ask, act is yes otherwise | `soon` |
| U3 | speak, ask and act are all no | `can wait` |

## 11. Rationale

One sentence, at most 300 characters, naming the rule IDs that decided `speak` and `ask`
and any other yes: `"S5 beer in hand late, persona cutting alcohol; I3 intake; W2 open drink."`

## 12. How states vary (for data generators)

Real states come from the pipeline, so synthetic ones must look like them. Measured on
the 141 real states from 24 Sep (02:07-02:10, Thursday, one persona):

- **Triggers.** 134 of 141 are `cue`, 5 `change`, 1 `medication_seen`, 1 `outdoor_sustained`.
  `trigger.reason` is empty for every `cue` and for some `change`; when present it is a
  short lower-case clause. Synthetic data should cover every trigger in §9 plus keyword
  triggers (named e.g. `vape_seen`, reason `Keyword 'vape' seen in caption/objects`), but
  keep `cue` the majority and keep empty reasons common.
- **recent.** Up to 6 entries, newest first, `age_s` climbing about 1.5 s per tick
  (`0.0, 1.6, 3.1, 4.6, ...`). About 1 in 50 entries is `no_ai`; include some states whose
  `recent[0]` is `no_ai`, and a few that are blind.
- **Captions.** Gemini Flash-Lite style: short, plain, often lower-case, sometimes without
  a period ("person holding an iphone while sitting"). Objects are 1-5 short nouns.
  Captions and tags sometimes disagree; keep a few of those.
- **Tags.** `true` lists only the booleans that are true, in the `ai_fields.py` order.
  `activity` is sometimes `null`. `hot` is usually `[]`.
- **No `food_type` or `drink` field.** The state never carries them; the drink or food
  type only shows through caption and objects.
- **episodes.** The label is often the truncated fallback `scene home, activity computer_use, food_`
  (40 chars). Several episodes can be open at once.
- **today.** Up to 12 lines, no timestamps. Kinds of line: descriptive annotations
  ("Phone in hand at the laptop; water and juice bottles visible."), `said: "..."`,
  `asked about <thing> -> "<answer>"`, `asked: <question> — no answer`. Starts empty in the
  morning; long sessions show the same scene paraphrased many times.
- **persona.** A 600-1200 char paragraph (often truncated mid-word at 1200) naming the
  wearer, goals, cutoffs, and what not to nag about. Vary it: early riser, shift worker,
  someone cutting alcohol, someone on medication, someone for whom late screens are normal.
- **trends.** A few lines of seven-day numbers (sleep, HRV, steps, daylight, screen hours,
  journal counts). Can be empty.
- **clock.** Spread across the whole day and week. Rules S5, S8, S9, C2, C3 and I5 all
  turn on the clock, so cover morning (daylight before 10:00), afternoon (caffeine cutoff),
  evening (alcohol), and late night (screens, wind-down).
- **Hard cases worth over-sampling:** the second and third escalation about the same
  object (repeat suppression), compliance right after a nudge, ambiguous "bottle" or
  "cup", a drink in hand during a conversation, and blind states.
