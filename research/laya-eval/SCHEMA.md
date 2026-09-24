# Labeled example format

One JSON object per line (JSONL). `scripts/validate.py FILE` checks every rule on this
page; `--state-only` checks only `id` and `state` (used for `data/real_states.jsonl`,
whose lines are `{"id", "real_decision", "state"}`).

## Top level

```json
{"id": "synth_b03_0042", "source": "synth", "state": {...}, "labels": {...}, "rationale": "S5 beer in hand; I3 intake; W2 open drink."}
```

| Key | Type | Rule |
|---|---|---|
| `id` | string | Non-empty, unique within the file. |
| `source` | string | `real` or `synth`. |
| `state` | object | Exactly the seven keys below, nothing else. |
| `labels` | object | Exactly the ten keys below. |
| `rationale` | string | Non-empty, at most 300 characters (RULEBOOK §11). |
| `real_decision` | object or null | Optional. Only allowed when `source` is `real` (kept from the render). |

No other top-level keys.

## `state`: exactly what `build_state` emits

Source: `backend/pipeline/reasoner/decider.py`, `build_state`. Keys in this order:
`trigger, recent, episodes, today, persona, trends, clock`. The validator rejects any
missing or extra key at this level and at every level below.

| Path | Type | Rule |
|---|---|---|
| `trigger` | object | Exactly `{name, reason}`. |
| `trigger.name` | string | snake_case (`^[a-z][a-z0-9_]*$`). Shipped names: `cue`, `change`, `food_in_frame`, `screen_sustained`, `people_sustained`, `outdoor_sustained`, `caffeine_seen`, `alcohol_seen`, `medication_seen`, `stillness`, `biometric_anomaly`, plus configured keyword triggers under their own names. |
| `trigger.reason` | string | May be empty (it is empty for every `cue` in the real data). |
| `recent` | list | 1 to 6 ticks, **newest first**: `age_s` never decreases along the list. |
| `recent[i]` (fresh) | object | Exactly `{age_s, true, scene, activity, caption, objects, hot}`. |
| `recent[i]` (stale) | object | Exactly `{"age_s": x, "true": [], "no_ai": true}`. |
| `age_s` | number | >= 0, one decimal in practice. Not a boolean. |
| `true` | list of strings | Distinct names from BOOL_FIELDS, in BOOL_FIELDS order. Only the true ones. |
| `scene` | string or null | SCENE enum. `null` happens when the reading is stale. |
| `activity` | string or null | ACTIVITY enum. `null` is common in real data (36 of 843 ticks). |
| `caption` | string | At most 120 characters (`CAPTION_MAX`). |
| `objects` | list of strings | Each at most 40 characters (`OBJECT_MAX`). |
| `hot` | list of strings | Each at most 40 characters. Usually `[]`. |
| `episodes` | list | Each exactly `{kind, minutes_open, label}`. |
| `episodes[i].kind` | string | EpisodeKind (below). |
| `episodes[i].minutes_open` | number | >= 0. |
| `episodes[i].label` | string | At most 40 characters. |
| `today` | list of strings | At most 12 lines (`max_summary`), each at most 160 characters (`SUMMARY_MAX`). |
| `persona` | string | At most 1200 characters (`PERSONA_MAX`). |
| `trends` | string | At most 1200 characters (`TRENDS_MAX`). |
| `clock` | object | Exactly `{local, weekday}`. |
| `clock.local` | string | `HH:MM`, 24-hour, 00:00 to 23:59. |
| `clock.weekday` | string | `Monday` ... `Sunday`. |
| whole state | | `len(json.dumps(state))` <= 12000 (`state_size_ok`, 3000 tokens at 4 chars each). |

### Enums (from `src/longevity/ai_fields.py`; the validator imports them from there)

- **BOOL_FIELDS** (order matters): `food_present, caffeine_visible, alcohol_visible,
  screen_present, vegetation_visible, people_present, people_interacting,
  direct_sunlight_visible, outdoor_visible, smoking_or_vaping_visible, medication_visible`.
  `phone_in_hand` is tri-state and is **not** in the state.
- **SCENE**: `home, office, classroom, library, lab, restaurant, cafe, bar, gym, store,
  grocery_store, hospital, hotel, park, trail, campus, street, parking_lot, beach, nature,
  sports_venue, construction_site, car, public_transit, airport, sauna, cold_plunge,
  indoor_other, outdoor_other, unknown`.
- **ACTIVITY**: `seated, standing, walking, running, cycling, driving, exercising,
  lifting_weights, stretching, eating, drinking, cooking, reading, computer_use,
  phone_use, talking, shopping, cleaning, lying_down, sleeping, personal_care, commuting,
  other, unknown`.
- **FOOD_TYPE** and **DRINK** exist in `ai_fields.py` but **never appear in the state**.
  Food and drink types reach the decider only through `caption` and `objects`.

### EpisodeKind (from `backend/pipeline/models.py`)

`meal, food_sighting, conversation, outdoor_block, screen_block, gym_session,
sauna_session, caffeine_sighting, alcohol_sighting, medication_sighting`.

## `labels`

| Key | Type | Values |
|---|---|---|
| `annotate`, `log_insight`, `remember`, `watch`, `speak`, `ask`, `act`, `look` | boolean | `true` / `false` |
| `topic` | string | `caffeine, food, alcohol, screen, people, outdoors, medication, sleep, movement, other` |
| `urgency` | string | `can wait, soon, now` |

All ten keys are required, no others. Urgency must also follow RULEBOOK §10: `can wait`
exactly when `speak`, `ask` and `act` are all false (the validator checks this).

## Differences from the pipeline worth knowing

- `today` lines carry no timestamps; "recent" means position (RULEBOOK §0).
- Episode labels are usually the 40-character fallback (`scene home, activity computer_use, food_`), cut mid-word.
- `persona` is cut at exactly 1200 characters, mid-word, in the real data.
