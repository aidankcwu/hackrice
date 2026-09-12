# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Brian
**Generated:** 2026-09-12 13:03:56
**Category:** General
**Design Dials:** Variance 3/10 (Centered / Minimal) | Density 7/10 (Standard)

---

## Global Rules

### Color Palette

The WHOOP reference is the brief: black wordmark bar, white page, soft grey
containers, one bold sans, and **colour reserved for data** — green earns, red
costs, everything else black or grey. No gauges, rings, neon, gradients, or
monospace. Tokens live in `dashboard/src/app/globals.css` (CSS variables and
Tailwind `@theme`) and `dashboard/src/lib/tokens.ts` (JS copy for charts).

| Role | Hex | CSS Variable | Tailwind | Use |
|------|-----|--------------|----------|-----|
| Page background | `#FFFFFF` | `--bg` | `bg-bg` | page |
| Surface | `#F4F4F5` | `--surface` | `bg-surface` | grey containers, 20px radius |
| Surface 2 | `#EAEAEC` | `--surface-2` | `bg-surface-2` | tracks, hover |
| Ink | `#111111` | `--ink` | `text-ink` | headings, primary numbers |
| Text | `#1F1F23` | `--text` | `text-text` | body |
| Muted | `#6B6B73` | `--muted` | `text-muted` | secondary text (5.6:1 on white, 5.1:1 on surface) |
| Line | `#E2E2E6` | `--line` | `border-line` | dividers |
| Earn | `#15803D` | `--earn` | `text-earn` | positive healthy-life hours (5.0:1 on white) |
| Earn soft | `#E8F5EC` | `--earn-soft` | `bg-earn-soft` | "Earned" chip fill |
| Cost | `#C62828` | `--cost` | `text-cost` | negative hours (5.9:1 on white) |
| Cost soft | `#FBEAEA` | `--cost-soft` | `bg-cost-soft` | "Cost" chip fill |
| Header | `#000000` | `--header` | `bg-header` | wordmark bar |

**Colour rules:** green/red are data colours only and never appear without a
second cue — every signed number carries `+`/`−`, bars sit above or below a
zero baseline, chips carry the words "Earned"/"Cost". Body text is never green
or red. No accent/CTA colour: the one primary action per screen is ink on white.

### Typography

- **Font:** DM Sans 400 / 500 / 700 / 800, loaded with `next/font/google` (`--font-dm-sans`), system sans fallback.
- **Scale:** 12 (captions only) · 14 (secondary) · 16 (body) · 20 (panel titles) · 24 (headline numbers) · 72 (today's hours).
- **Rules:** body text ≥14px, no ALL-CAPS labels, no monospace, tabular figures on numbers (`.tnum`).

### Radius & icons

- Panels 20px, pins 16px, chips 9999px.
- Icons: lucide-react, 18px in lists, stroke 2, `aria-hidden` beside visible text. No emoji.
- Every button ≥44px tall, visible 2px ink focus ring, `prefers-reduced-motion` disables transitions.

### Spacing Variables

*Density: 7/10 — Standard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |
| `--space-md` | `16px` / `1rem` | Standard padding |
| `--space-lg` | `24px` / `1.5rem` | Section padding |
| `--space-xl` | `32px` / `2rem` | Large gaps |
| `--space-2xl` | `48px` / `3rem` | Section margins |
| `--space-3xl` | `64px` / `4rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Buttons

```css
/* Pill button — the only button style. Ink on white inside grey panels,
   white on black inside the header. Never green or red. */
.btn {
  min-height: 44px;
  padding: 0 16px;
  border-radius: 9999px;
  background: var(--bg);
  color: var(--ink);
  font-weight: 500;
  font-size: 14px;
  transition: background-color 200ms ease;
  cursor: pointer;
}
.btn:hover { background: var(--surface-2); }
.btn:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
```

### Panels

```css
/* Grey container, no border, no shadow, no hover lift. */
.panel { background: var(--surface); border-radius: 20px; padding: 24px; }
@media (min-width: 768px) { .panel { padding: 32px; } }

/* White row inside a panel; hover darkens the background only. */
.tile { background: var(--bg); border-radius: 16px; transition: background-color 200ms ease; }
.tile:hover { background: var(--surface-2); }
```

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #171717;
  outline: none;
  box-shadow: 0 0 0 3px #17171720;
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Minimalism & Swiss Style

**Keywords:** Clean, simple, spacious, functional, white space, high contrast, geometric, sans-serif, grid-based, essential

**Best For:** Enterprise apps, dashboards, documentation sites, SaaS platforms, professional tools

**Key Effects:** Subtle hover (200-250ms), smooth transitions, sharp shadows if any, clear type hierarchy, fast loading

### Page Pattern

**Pattern Name:** Hero + Features + CTA

- **Conversion Strategy:** Deep CTA placement. For CTA label text, verify at least 4.5:1 against the button fill; use 7:1 only when the product explicitly targets AAA normal-text contrast. Keep focus and component boundaries independently visible. Disable hero parallax under reduced motion and render its static final state.
- **CTA Placement:** Hero (sticky) + Bottom
- **Section Order:** Hero with headline/image > Value prop > Key features (3-5) > CTA section > Footer

---

## Anti-Patterns (Do NOT Use)


### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
