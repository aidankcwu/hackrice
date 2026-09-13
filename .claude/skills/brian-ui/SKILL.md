---
name: brian-ui
description: Design and copy system for Project Bryan, the healthspan dashboard fed by Ray-Ban Meta glasses and WHOOP. Use whenever building, restyling, reviewing, or writing copy for any Bryan screen, panel, tile, menu, empty state, or spoken nudge. Encodes the visual tokens, the page order that puts the glasses-only layers first, every panel's exact copy, the ear-nudge taxonomy, and the pre-delivery checklist merged from Vercel's Web Interface Guidelines and ui-ux-pro-max.
---

# Bryan UI — the master designer skill

You are the design lead for a product that must look like Apple shipped it and read like Stripe wrote it. The dashboard exists to make one thing obvious in three seconds: **the glasses see layers of health nothing else can** — your clock, your light, the people around you, the air and nature you're in, your mind. Steps and sleep are table stakes; they never lead.

Read `references/screens.md` before touching layout, `references/voice.md` before writing any string, `references/nudges.md` before touching anything the ear says, and run `references/checklist.md` before calling anything done.

## The five laws

1. **New first.** Above the fold on Today: the ledger number and the five glasses-only instruments (Clock, Light, People, Outside, Mind). Wearable-known numbers (steps, strain, sleep stages) live below, under a heading that admits it: "The numbers your wearable already knows."
2. **Headings are what the user is looking for.** Nouns, not narration. "Your clock", "People", "Tonight", "This week". Never "Where it came from", never "Insights", never "Overview", never "Dashboard".
3. **Every number has a reason and a source.** A value without its "because" and its provenance chip (Glasses · WHOOP · Entered · Seeded · Imputed) is a bug.
4. **Restraint is visible.** Show what Bryan chose not to say as clearly as what it said. "Held back 14 today" is a feature.
5. **Nothing looks generated.** No gauges, rings, gradients, neon, monospace labels, emoji, ALL-CAPS eyebrows, or identical card grids. Rules and whitespace do the structure; colour is reserved for data.

## Tokens (source of truth)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FFFFFF` | page |
| `--surface` | `#F4F4F5` | panels, 20 px radius |
| `--surface-2` | `#EAEAEC` | tracks, hover, dividers inside panels |
| `--ink` | `#111111` | headings, primary numbers, header bar text on white |
| `--text` | `#1F1F23` | body |
| `--muted` | `#6B6B73` | secondary; ≥ 4.5:1 on white |
| `--line` | `#E2E2E6` | table rules |
| `--earn` / `--earn-soft` | `#15803D` / `#E8F5EC` | positive hours, "Earned" chip |
| `--cost` / `--cost-soft` | `#C62828` / `#FBEAEA` | negative hours, "Cost" chip |
| `--clock` | `#1D4ED8` | the circadian instrument only (one accent, one meaning) |
| header | `#000000` | wordmark bar, 64 px |
| font | DM Sans 400 / 500 / 700 / 800, `next/font/google`, `font-display: swap` | all text; `tabular-nums` on every number |
| type scale | 72 hero number · 40 tile number · 24 section title · 16 body · 14 secondary · 12 caption | never below 12; mobile inputs ≥ 16 |
| radius | 20 panel · 16 tile/pin · 9999 chip | nested radius = outer − padding |
| spacing | 4-pt grid; panel padding 32 desktop / 20 mobile; gap 24 between panels, 12 inside | |
| grid | 12 columns, max-width 1200, gutter 24; single column < 768 | |
| icons | lucide-react, 18 px in rows, 20 px in tiles, stroke 2, `aria-hidden` | one icon per layer, fixed: Clock=Clock, Light=Sun, People=Users, Outside=Trees, Air=Wind, Mind=Brain, Body=Footprints, Sleep=Moon, Fuel=Utensils, Recovery=Flame |
| motion | 200 ms, `transform`/`opacity` only, honour `prefers-reduced-motion` | hover lifts by background change, never shadow |
| hit targets | ≥ 44 px mobile, ≥ 24 px desktop | |
| focus | `focus-visible` 2 px ink ring, 2 px offset | never `outline-none` alone |

## Page order and what each block is for

1. **Header** — BRYAN wordmark · Today · Week · Evidence · Plan · How it's scored · status dot (live / seeded) · profile chip. Ticks and model names go in the Pipeline drawer, never the header.
2. **Today ledger** (5 cols) — the number, the sentence, the two currencies.
3. **What only your glasses can see** (7 cols) — five instrument tiles. This is the product.
4. **What Bryan said** — the ear log with outcomes and the held-back count.
5. **What the glasses saw** — evidence pins, merged episodes, ≤ 12 on Today.
6. **Tonight** (with counterfactual sliders) · **Next best minutes**.
7. **By layer** — the nine layer rows.
8. **The numbers your wearable already knows** — collapsed by default.
9. **This week** · **What moves you**.
10. **Last seven days** with narrator arrows.
11. Footer — provenance legend, "How the hours are computed".

Full specs, states, and exact strings: `references/screens.md`.

## Copy, in one line each

Second person, present tense, numerals, one sentence per idea, name the object the camera saw, state the number it moved, cite in five words or fewer, never shame, never hedge with "may/might/could" when the engine gave a number. Details: `references/voice.md`.

## Do not

- Put steps, strain, or a step ring anywhere above the instruments.
- Render a metric without provenance.
- Show a forecast outside its clamps (sleep 3.5–10 h, HRV −40 to +10 %, clock 0–120 min).
- Count sightings as units (the engine merges episodes; "55 drinks" is a pipeline bug, never a display).
- Use "AI", "reasoner", "model", "confidence" on any user-facing surface; those live in the Pipeline drawer.
- Ship a panel without its empty, loading, unmeasured, and seeded states.
