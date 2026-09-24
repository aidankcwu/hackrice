# Generator brief (shared by every synthetic-data agent)

Write ~150 labeled states for your assigned THEME into data/synth/batch_<NN>.jsonl
(NN = your two-digit batch number). Format: SCHEMA.md, "source": "synth",
ids "synth_<NN>_<k>". Labels: apply RULEBOOK.md literally to EACH state.

Realism (the model will be judged on REAL states, so imitate them):
- Look at 3-4 lines of data/real_states.jsonl (python + json, print only the
  `recent` and `today` parts) to copy the style: short Gemini captions, sometimes
  lowercase or odd ("person holding an iphone while sitting"), 1-5 objects,
  `true` = subset of the real BOOL_FIELDS names, scene/activity from the enums in
  SCHEMA.md, occasional {"age_s":..,"true":[],"no_ai":true} ticks, age_s rising
  ~1.5 s per tick newest-first, episode labels like
  "scene home, activity computer_use, food_" cut at 40 chars, `hot` = [].
- `today` lines look like: plain notes ("Drinking water at the laptop."),
  `said: "Phone down, water bottle first."`, and
  `asked about <topic> -> "<wearer's answer>"`. 0-12 lines.
- persona and trends: pick from data/pools.json (vary across your batch;
  use every persona at least 15 times). clock: vary time of day and weekday
  to fit the theme (late-night caffeine, morning daylight, etc.).
- trigger.name from: cue, change, food_in_frame, screen_sustained,
  people_sustained, outdoor_sustained, caffeine_seen, alcohol_seen, stillness,
  biometric_anomaly, keyword; trigger.reason usually "" (real data: 132/141
  empty), sometimes a short phrase.

Label balance targets for your batch (print them; adjust if far off):
annotate yes 55-75%, speak yes 20-35%, ask yes 8-20%, look 8-20%,
log_insight 15-30%, remember 8-20%, watch 10-25%, act 5-15% (more in act-themed
batches), urgency spread across all 3 levels.

Contrast pairs are the most valuable examples: the same scene twice where one
detail flips the answer (a matching `said:` line in the last 6 today lines;
compliance already logged; persona that does/doesn't care; 09:00 vs 23:30 for
caffeine; caption "holding a cookie near mouth" vs "cookie on a plate across
the room"). Make at least 30% of your batch contrast pairs.

How: write a Python script in the scratchpad that holds your hand-written
scenario content and assembles JSON. Hand-decide each label (or encode the
rulebook rule explicitly in code for programmatic variants and spot-check 15).
Never copy a real state verbatim. No two states may have identical `recent`.

Finish: `python3 scripts/validate.py data/synth/batch_<NN>.jsonl` must print OK.
Write data/synth/batch_<NN>.stats.json (count + yes-rates + urgency/topic
counts). Append ONE line to progress.txt:
`HH:MM gen batch_<NN> (<theme>) done: n=<count>, speak=..% ask=..%`.
Do NOT edit prd.json, REPORT.md, RULEBOOK.md or any other batch.
Return to the orchestrator in <= 60 words: count, validator result, yes-rates
for speak/ask/act, anything odd.
