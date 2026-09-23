---
name: brian-ios-design
description: The look, feel and copy of the Brian phone app (phone/ on the web, ios/Brian native). Load before writing or reviewing any screen. When another design skill disagrees with this one, this one wins. Also the rubric the design-critic grades screenshots against.
---

# Brian on iOS

The brief: the calm of a medical instrument, the restraint of the best health apps, and
nothing that reads as generated. Colour is reserved for data. Palette, type and spacing
were picked in task F.0: direction C · Instrument (`phone/design/directions.html`), with
A's green/red pair for the sign.

The app has one job: show the user what the system handled for them today, and make
starting it a single tap. It is not a chat app, not a dashboard, not a coaching feed.

## Starting point: none

Nothing that existed before this plan is a reference: not the judges' dashboard in
`dashboard/`, not `design-system/brian/`, not the `brian-ui` skill. Do not read them for
look, layout, components or copy. The look was decided in task F.0 with the human and is
written into the Tokens section below.

## Non-negotiables

1. Native first. `NavigationStack`, `List(.insetGrouped)`, system sheets, system
   toolbars, standard Liquid Glass controls (iOS 26 SDK default). Custom chrome only
   when the spec names it.
2. One primary action per screen. It is the only `.glassProminent` button on screen.
   Everything else is `.glass`, plain, or a list row.
3. Status is always visible without a tap. Glasses, backend, watching: one line each.
4. Every error names the fix and offers one button. "Backend unreachable. Check that the
   Mac and phone share Wi‑Fi." [Try again]. Never a bare "Something went wrong".
5. Signed numbers carry `+`/`−` and a word. Colour is never the only cue.
6. Dynamic Type through accessibility XXXL without clipping; 44 pt targets; VoiceOver
   labels on every icon-only control; `Reduce Motion` disables every animation.
7. Dark mode is a first-class appearance, tokens below cover both.
8. No frames, thumbnails, or video are ever written to disk by a view. Thumbnails come
   from the backend evidence route and are shown, not cached.

## Banned (this is the "does not look like AI" list)

Gradients. Rings, gauges, dials, progress circles. Emoji anywhere, including icons.
Purple, teal, neon, "brand" accent colours. Cards inside cards. Drop shadows on panels.
Hero illustrations, mascots, blobs. Onboarding carousels with dots. Confetti, celebrations,
streak flames. Skeleton shimmer. Floating action buttons. Five-tab bars. Chat bubbles or
an "assistant" persona. Greetings ("Welcome back!"). Exclamation marks. ALL‑CAPS labels.
Monospace anywhere except nothing (numbers use tabular figures, not monospace). Lorem
ipsum. Stock or generic avatars. Toasts that stack. Custom fonts.

## Tokens (F.0: direction C · Instrument, A's green/red pair; paste as `ios/Brian/Sources/Theme/Theme.swift`, mirrored in `phone/src/app/globals.css`)

```swift
import SwiftUI
import UIKit

/// Brian palette: direction C · Instrument, picked in F.0, with A's green/red pair for the sign.
/// Data colours only ever appear next to a sign and a word that carry the meaning too.
enum Brian {
    static let page     = Color(light: 0xFFFFFF, dark: 0x000000)
    static let surface  = Color(light: 0xF5F5F7, dark: 0x1A1A1C)   // panels (the hero), 20 pt radius
    static let surface2 = Color(light: 0xE8E8ED, dark: 0x29292C)   // capsule chips, pressed rows
    static let ink      = Color(light: 0x0A0A0A, dark: 0xF5F5F7)   // headings, 0.0 h, primary button fill
    static let text     = Color(light: 0x1D1D1F, dark: 0xE3E3E8)   // body
    static let muted    = Color(light: 0x636368, dark: 0x939399)   // secondary text, times, held-back rows
    static let line     = Color(light: 0xE3E3E8, dark: 0x2C2C2E)   // hairlines between ledger rows
    static let earn     = Color(light: 0x047857, dark: 0x4ADE80)   // positive hours only
    static let earnSoft = Color(light: 0xE3F2EB, dark: 0x0E2A1C)   // fill behind a signed gain chip
    static let cost     = Color(light: 0xC81E1E, dark: 0xF87171)   // negative hours only
    static let costSoft = Color(light: 0xFBE7E7, dark: 0x301313)   // fill behind a signed cost chip

    static let panelRadius: CGFloat = 20
}

/// Spacing in points, on the 4 / 8 / 16 / 24 / 32 grid.
enum Space {
    static let gutter: CGFloat = 24       // screen edges
    static let section: CGFloat = 32      // between status strip, button, hero, ledger
    static let panel: CGFloat = 24        // panel padding
    static let statusRow: CGFloat = 28    // status strip line, min height
    static let logRow: CGFloat = 52       // ledger row, min height
    static let timeColumn: CGFloat = 40   // ledger time column; then 8 · symbol 18 · 16 · title · 8 · outcome
}

enum BrianType {
    /// Today's hours. The one fixed-size number in the app; everything else is a text style.
    static let hero = Font.system(size: 80, weight: .semibold).monospacedDigit()
    static let heroTracking: CGFloat = -2.5
    static let number = Font.title2.weight(.semibold).monospacedDigit()
    static let title = Font.title3.weight(.semibold)     // 20
    static let body = Font.body                          // 17
    static let secondary = Font.subheadline              // 15; ledger times add .monospacedDigit()
    static let caption = Font.footnote                   // 13
    static let outcome = Font.footnote.weight(.medium)   // 13, symbol + word, no capsule
    static let chip = Font.caption.weight(.medium)       // 12, capsule
}

extension Color {
    init(light: UInt32, dark: UInt32) {
        self.init(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light)
        })
    }
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255,
                  green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255,
                  alpha: 1)
    }
}

/// Grey panel. No border, no shadow, no hover.
struct Panel: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(Space.panel)
            .background(Brian.surface, in: RoundedRectangle(cornerRadius: Brian.panelRadius, style: .continuous))
    }
}

extension View {
    func panel() -> some View { modifier(Panel()) }
}

/// Hours are rounded to 0.1 before anything else, so −0.02 reads "0.0 h", never "−0.0 h".
enum Hours {
    static func rounded(_ hours: Double) -> Double { (hours * 10).rounded() / 10 }
    /// The word next to the hero number. The sign and this word carry the meaning; colour repeats it.
    static func word(_ hours: Double) -> String {
        rounded(hours) < 0 ? "healthy life cost today" : "healthy life earned today"
    }
}

/// Signed hours: "+1.0 h" in earn green, "−1.1 h" in cost red, "0.0 h" in ink.
struct SignedHours: View {
    let hours: Double
    var font: Font = BrianType.number

    var body: some View {
        let r = Hours.rounded(hours)
        let sign = r > 0 ? "+" : (r < 0 ? "−" : "")
        let magnitude = String(format: "%.1f", abs(r))
        Text(sign + magnitude + " h")
            .font(font)
            .foregroundStyle(r > 0 ? Brian.earn : (r < 0 ? Brian.cost : Brian.ink))
            .accessibilityLabel(r > 0 ? "plus \(magnitude) hours" : (r < 0 ? "minus \(magnitude) hours" : "\(magnitude) hours"))
    }
}
```

## Layout rules

- Screen background is `Brian.page`, gutters `Space.gutter` (24). Section gaps 32.
  One panel on Today: the hero (`Brian.surface`, `Brian.panelRadius`, padding 24). The
  status strip and the ledger sit on the page, not in panels.
- Spacing: 4 / 8 / 16 / 24 / 32.
- One hero number per screen (Today only): `BrianType.hero` with `heroTracking`. Label
  under it in `BrianType.secondary`, `Brian.muted`: `Hours.word(hours)`, i.e. "healthy
  life earned today" or "healthy life cost today". Provenance capsule beside the label.
- Status strip: three lines, symbol 16 pt muted, text `.subheadline`, min height 28.
- Ledger is a timed log: time (`.subheadline`, muted, tabular, 40 pt column) · SF Symbol
  18 pt muted · title in `.body` · trailing outcome in `BrianType.outcome` (symbol +
  word, no capsule). Full-width `Brian.line` hairlines between rows, min height 52.
  Held-back rows go grey: title in `Brian.muted`, muted words "held back", no symbol.
- Capsule chips (12 pt, `BrianType.chip`) are for provenance and signed values only:
  fill `surface2` (neutral), `earnSoft` (gain), `costSoft` (cost). Never green/red text
  in body copy.
- Destructive actions in sheets (Delete) are the single exception to "colour is for data": system destructive style, `Button(role: .destructive)`, system red.
- Toolbar and the primary button use Liquid Glass (`.glass`, `.glassProminent`). Content
  surfaces never use glass. Never glass on glass.
- Icons: SF Symbols only. Trigger families map to exactly these symbols:
  food `fork.knife`, caffeine `cup.and.saucer.fill`, alcohol `wineglass.fill`,
  outdoor `sun.max.fill`, screen `display`, people `person.2.fill`, biometric
  `heart.fill`, medication `pills.fill`, wind‑down `moon.fill`, walk `figure.walk`,
  glasses `eyeglasses`, backend `desktopcomputer`, watching `record.circle`.
  Outcomes: whispered `waveform`, asked `questionmark.bubble`, acted `checkmark.seal.fill`,
  held back: no icon, muted words "held back".

## Copy rules

Sentence case. Plain words a tired person reads in one pass. Numbers, not adjectives.
Say what happened, not what the system "thinks". Verbs on buttons: "Start watching",
"Stop", "Try again", "Open Meta AI", "Allow", "Undo", "Mark done", "Add item".
Status lines are noun + state: "Glasses connected", "Backend 10.0.0.5", "Watching 14 min".
Errors: one sentence of cause, one button of fix.
Empty states are instructions, not consolation. Today, nothing yet: "Put the glasses on.
Counting starts the moment the camera is up." with the button above it. Never "No
data available", never an illustration. Vocabulary was settled in F.0 with the human
and is the list at the top of `docs/IOS_SPEC.md`; it wins over any other string.
Whisper text comes from the backend and is never rewritten on the phone.

## The design-critic rubric (score each 0–2, report total /20)

1. One primary action, and it is obvious in under a second.
2. Status strip readable without scrolling, in light and dark.
3. Hero number correct sign, colour and word; nothing else on the screen is coloured.
4. No banned element present (scan the whole screenshot against the list above).
5. Text survives accessibility XXXL: no clipping, no overlapping, no truncated numbers.
6. Dark mode: panels visible, dividers visible, no pure‑white glare, no washed glass.
7. Empty and error states are instructions with a button.
8. Icons are SF Symbols, from the mapping above, with labels for VoiceOver.
9. Spacing on the 4/8/16/24/32 grid; panels 20 pt radius; targets ≥ 44 pt.
10. Would this screenshot pass as a screen from a shipping Apple-designed app? If it
    reads as "AI made this" in any way, say which element and why. A string that breaks
    the vocabulary settled in F.0, or says demo, N/A, or carries an exclamation mark,
    scores this item 0.

A screen ships at ≥ 16/20 with no item scored 0.
