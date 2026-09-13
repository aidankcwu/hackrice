# Pre-delivery checklist — merged from Vercel Web Interface Guidelines and ui-ux-pro-max

Run on every changed file. Output `file:line - issue`. No preamble.

## Bryan-specific (fail any → not done)
- Above the fold on Today: ledger + the five instrument tiles. No steps/strain/ring above them.
- Every metric shows a provenance chip (Glasses · WHOOP · Entered · Seeded · Imputed).
- Every panel has loading, empty, unmeasured, and seeded states with the exact strings from voice.md.
- No user-facing "AI", "model", "reasoner", "confidence", "ticks", latency.
- Forecast values inside clamps; no sighting counts shown as units.
- Headings are nouns the user looks for; no "Where it came from", "Overview", "Insights".
- One accent colour per meaning: earn green, cost red, clock blue. Nothing else coloured.
- Numbers `tabular-nums`; `…` not `...`; curly quotes.

## Accessibility
- Icon-only buttons `aria-label`; decorative icons `aria-hidden`.
- `<button>` for actions, `<a>/<Link>` for navigation; never `<div onClick>`.
- Images: `alt`, explicit `width`/`height`, `loading="lazy"` below fold.
- Visible `focus-visible` ring; never `outline-none` alone; sticky header must not cover focus.
- Headings hierarchical; skip link to main; async updates `aria-live="polite"`.
- Sliders: keyboard steps, labelled, value announced.

## Interaction
- Hit targets ≥ 44 px mobile, ≥ 24 px desktop; `touch-action: manipulation`.
- Hover states on every button/link; active/focus more prominent than rest.
- Destructive actions (delete frames) need confirmation or undo.
- URL reflects tabs, filters, expanded panels; Cmd/Ctrl-click works on links.
- Loading indicator show-delay 150–300 ms, min visible 300–500 ms.

## Motion
- `prefers-reduced-motion` honoured; animate `transform`/`opacity` only; never `transition: all`.

## Forms (Profile, Labs, PVT check)
- Labels clickable; correct `type`/`inputmode`; mobile inputs ≥ 16 px; no paste blocking; errors inline with the fix.

## Layout & content
- `min-w-0` on flex children that truncate; long strings `truncate`/`line-clamp`.
- No unwanted horizontal scroll except the pins strip (with visible scrollbar + keyboard arrows).
- Safe-area insets on the mobile tab bar.
- Dates/numbers via `Intl.*`.

## Performance
- Fonts preloaded with `font-display: swap`; preconnect to CDNs.
- Lists > 50 rows (Evidence full day) virtualised.
- No layout reads in render.

## Visual QA (screenshots at 375 and 1440)
- Panels 20 px radius, 32/20 padding; nested radii = outer − padding.
- Type scale respected; nothing under 12 px; body 16 on mobile.
- Contrast ≥ 4.5:1 for text, ≥ 3:1 for large numbers and chips.
- Nothing looks generated: no gradients, gauges, neon, emoji, ALL-CAPS labels, identical card grids.
