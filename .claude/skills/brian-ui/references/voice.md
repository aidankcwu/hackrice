# Voice — how Bryan writes

Model: Apple's Health app headings, Stripe's precision, a good doctor's restraint.

## Rules
- Second person, present tense. "You had 44 bright minutes." not "The user received…"
- Numerals always: "3 people", "41 min", "+0.4 h". Units abbreviated after a thin space: `41 min`, `6.3 h`, `31 µg/m³`.
- One idea per sentence. The number, then the reason, then the source in ≤ 5 words in muted text.
- Name the object the camera saw: "the second coffee", "the phone in the dark", "the park". Never "an item".
- Cite like this, always muted, always short: `Drake 2013`, `Holt-Lunstad 2010`, `WHO 2021`.
- Never shame. Every cost line ends with the cheapest recovery: "Two drinks cost about 0.4 h. Water and bed by 23:00 gets most of it back."
- No hedging when the engine produced a number. Forecasts say "about", never "may".
- No exclamation marks. No emoji. No "great job". No "let's".
- Headings: sentence case nouns. Buttons: verb + object, Title Case only when 2 words or fewer ("Place It", "Full Day", "Take Test").
- Loading ends with an ellipsis character: "Scoring today…". Quotes are curly.
- Dates and numbers through `Intl.*`; never hardcode "09/12".

## Words we use → words we don't
| Use | Don't |
|---|---|
| earned / cost | gained / lost points |
| healthy-life hours | score points, XP |
| fully-lived hours | wellness score |
| your clock | circadian phase (except on How it's scored) |
| people, conversation | social graph, contacts |
| outside, air, noise | environmental exposome (except How it's scored) |
| mind, reaction time | cognitive performance |
| Bryan said / held back | AI decided / the model spoke |
| seeded | fake, demo, mock |
| unmeasured | N/A, null, missing |

## Empty and edge states (exact strings)
- No glasses data yet: "Put the glasses on. Bryan starts counting light, people, and air the moment the camera is up."
- No WHOOP: "Connect WHOOP to see tonight's forecast checked against real sleep."
- Unmeasured factor: "Unmeasured today — scored at the population average, earns nothing."
- Seeded row: chip "Seeded" + tooltip "Sample data for layout. Not you."
- Fewer than 14 days for attribution: "Population estimate. Your own number appears at 14 days — 9 to go."
- PVT stale > 24 h: "Take the 3-minute test to update Mind."
- Quiet hours: "Bryan is quiet until 07:00."
