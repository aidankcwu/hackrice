# Measured findings

Things established by measurement rather than reasoning. Update as more land.

## BUG — `gemini-flash-lite-latest` 400s on every call. Stay on `gemini-2.5-flash-lite`.

`gemini-flash-lite-latest` rejects `thinking_config=ThinkingConfig(thinking_budget=0)`
with `400 INVALID_ARGUMENT`. Reproducible, 8/8 calls. Drop the thinking option and it
works; keep it and every call fails.

`vlm.py` sets `thinking_budget=0` on purpose: T0 has a 1 s budget and the task is
"report what is plainly visible", so there is nothing to reason about and thinking is
pure latency. We want to keep it.

**Do not "upgrade" `DEFAULT_MODEL` to the `-latest` alias.** It reports its version as
`"Gemini Flash-Lite Latest"` where `gemini-2.5-flash-lite` reports `"001"` — it is a
moving target that can repoint mid-hackathon. We are pinned deliberately.

What makes this bug nasty: it fails *silently in the correct way*. The drop rule works,
so ticks keep flowing at exactly 1 Hz, `sensor` is untouched, nothing crashes. The ticks
just never carry an `ai` block. Coverage reads 0% and B's triggers simply never fire —
which looks like a bug on B's side. Watch the `coverage=` line in the run log.

## OPEN ISSUE — AI coverage sits right on the 1 s budget line

§2.4 expects 50–80% of ticks to carry an `ai` block. Measured coverage on
`gemini-2.5-flash-lite` against real corpus frames:

| Config | n | p50 | under 1 s |
|---|---|---|---|
| no `thinking_budget` | 4 | 1176 ms | 25% |
| `thinking_budget=0` | 8 | 977 ms | 50% |
| no `thinking_budget` | 8 | 824 ms | 100% |

That 25–100% spread is not a model difference — it is run-to-run variance. Every
flash-lite variant clusters around 800–1100 ms, and the budget is exactly 1000 ms, so
the threshold cuts through the middle of the latency distribution and a ~100 ms network
shift swings coverage by tens of points. **Do not rank models on this metric at small
n; I did, and was wrong.** Caveat on all of it: one network, one location, small n.
Re-measure on the venue Wi-Fi.

Untested: whether latency also depends on image content. The runs above used different
frames at different times, so content and network are confounded. The clean experiment
is the same 8 frames twice, back to back.

### The decision to make — two knobs, pick one

Coverage is short because calls land near the budget. Two ways out, and they are not
equivalent:

**(a) Raise `VLM_BUDGET_S` to ~1.5 s** (in `tick.py`, independent of the loop rate).
Coverage goes to roughly 80%+. Ticks stay at 1 Hz, so nothing downstream retunes. Cost:
staler tags, `age_ms` up to ~1500 instead of ~1000 — which §12.2 already tells B to
handle by decaying confidence with age. Cost: contradicts CLAUDE.md invariant 3 and
§2.4, both of which name 1 s explicitly.

**(b) Slow ticks to 1.5 s.** Also lifts coverage. Cost: §3 triggers are stateful over a
window of *ticks*, and §10 requires the episode builder to share the gate's debounce
parameters — so "5 ticks of stillness" silently becomes 7.5 s instead of 5 s and every
threshold B set shifts by 50%. Cost: 33% fewer ticks inside the four-minute demo window
§13.5 warns about. Contradicts §2.1 ("exactly one timestamp object per second").

A (Person A) leans (a) — it buys the same coverage without retuning anything downstream,
and §12.2 was written to absorb exactly this staleness. B owns the gate and the episode
builder, so **B makes the call.** Whichever is chosen, it is cheap to decide now and
expensive to decide Saturday, because B has not yet tuned against real ticks.

## §9 survives glasses POV

The field set works on real first-person frames. Spot-checked against the source
images: `screen_present`, `people_present`, `food_present`, `caffeine_visible` and
`food_type` all came back correct on frames where those things were plainly visible —
including `food_type: vegetables` for a bag of tomatoes on a table several feet away,
at 288x512, in poor indoor light. Tagging accuracy is not the weak link here.

One caveat: **`conf` does not discriminate.** Every call returned 0.80–0.95 regardless
of how ambiguous the frame was. It is not a usable confidence signal as prompted. B
should not write a trigger that thresholds on `ai.conf`. (Note `conf` appears in §12's
tick example but is absent from §9's field table — this is the gap CLAUDE.md warns
about. It may be worth dropping rather than fixing.)

## Glasses frame geometry

DAT frames are **portrait**, not landscape — all documented stream resolutions are
(360x640, 504x896, 720x1280). After the §2.2 resize to 512 px on the longest edge,
frames are **288x512**. Correctly oriented, no rotation needed.

Corpus frames encode to ~29 KB median at q70, under the ~40 KB estimate in §2.2 —
indoor scenes compress well. Budget 40 KB for outdoor/high-detail frames.
