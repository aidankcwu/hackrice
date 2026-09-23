# CLAUDE.md

The app lives in `phone/` (Next.js). Vision: `docs/VISION.md`. Old plan and specs: `docs/archive/`.

Commands (from `phone/`):
- `NEXT_PUBLIC_FIXTURES=1 npm run dev -- -p 3100` (fixtures mode, no backend needed)
- `npm run lint`
- `npx tsc --noEmit`

Design tokens (F.0, direction C · Instrument with A's green and red), in `phone/src/app/globals.css`:
page white/black, surface #f5f5f7/#1a1a1c, ink #0a0a0a, text #1d1d1f, muted #636368,
line #e3e3e8, earn #047857 (green: inside a window, gains), cost #c81e1e (red: violations, costs),
watch amber (only for "watch this"), panel radius 20 px, gutter 24, system font (SF Pro).
Every signed number carries a word. "seeded", never "demo".

Banned: gradients, rings, gauges, emoji, purple, cards in cards, shadows, greetings,
exclamation marks, all-caps, chat bubbles.

Rule: Build directly. Lint and tsc at the end of a task only. Screenshots only when asked.
Commit after each task.
