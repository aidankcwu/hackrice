---
name: brian-ios-design
description: The look, feel and copy of the Brian iOS app (ios/Brian). Load before writing or reviewing any SwiftUI view. When another design skill disagrees with this one, this one wins. Also the rubric the design-critic grades screenshots against.
---

# Brian on iOS

Brian is the native sibling of the dashboard in `design-system/brian/MASTER.md`. Same
brief: WHOOP-grade restraint. White page, soft grey panels, one sans, and colour
reserved for data. Green earns, red costs. Everything else is ink or grey.

The app has one job: show the user what the system handled for them today, and make
starting it a single tap. It is not a chat app, not a dashboard, not a coaching feed.

## Inherits from `brian-ui` (the dashboard's skill, already in this repo)

Its five laws and its vocabulary apply here unchanged. Before writing any string, read
`.claude/skills/brian-ui/references/voice.md` and use its words: earned / cost,
healthy‑life hours, "Bryan said" / "held back" (never spoke / silent / the model),
seeded (never demo, fake, mock on a user-facing surface), unmeasured (never N/A).
Every number carries its provenance chip: Glasses · WHOOP · Health · Entered · Seeded.
The one accent with one meaning, `clock` blue, is reserved for the circadian instrument
exactly as on the dashboard. This file adds what is iOS-specific and wins only there.

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
ipsum. Placeholder avatars. Toasts that stack. Custom fonts.

## Tokens (paste as `ios/Brian/Sources/Theme/Theme.swift`)

```swift
import SwiftUI
import UIKit

/// Brian palette. Mirrors design-system/brian/MASTER.md (light) with a dark twin.
/// Data colours only ever appear next to a sign or a word that carries the meaning too.
enum Brian {
    static let page     = Color(light: 0xFFFFFF, dark: 0x000000)
    static let surface  = Color(light: 0xF4F4F5, dark: 0x1C1C1E)   // panels, 20 pt radius
    static let surface2 = Color(light: 0xEAEAEC, dark: 0x2C2C2E)   // tracks, pressed rows
    static let ink      = Color(light: 0x111111, dark: 0xF5F5F7)   // headings, primary numbers
    static let text     = Color(light: 0x1F1F23, dark: 0xE5E5EA)   // body
    static let muted    = Color(light: 0x6B6B73, dark: 0x9A9AA3)   // secondary text
    static let line     = Color(light: 0xE2E2E6, dark: 0x2C2C2E)   // dividers
    static let earn     = Color(light: 0x15803D, dark: 0x4ADE80)   // positive hours only
    static let earnSoft = Color(light: 0xE8F5EC, dark: 0x0F2A1A)   // "Earned" chip fill
    static let cost     = Color(light: 0xC62828, dark: 0xF87171)   // negative hours only
    static let costSoft = Color(light: 0xFBEAEA, dark: 0x2A1010)   // "Cost" chip fill
    static let clock    = Color(light: 0x1D4ED8, dark: 0x60A5FA)   // circadian instrument only, nothing else

    static let panelRadius: CGFloat = 20
    static let tileRadius: CGFloat = 16
}

enum BrianType {
    /// Today's hours. The one fixed-size number in the app; everything else is a text style.
    static let hero = Font.system(size: 64, weight: .bold).monospacedDigit()
    static let number = Font.title2.weight(.bold).monospacedDigit()
    static let title = Font.title3.weight(.semibold)
    static let body = Font.body
    static let secondary = Font.subheadline
    static let caption = Font.footnote
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
            .padding(20)
            .background(Brian.surface, in: RoundedRectangle(cornerRadius: Brian.panelRadius, style: .continuous))
    }
}

extension View {
    func panel() -> some View { modifier(Panel()) }
}

/// Signed hours: "+1.4 h" in earn green, "−0.6 h" in cost red, "0.0 h" in ink.
struct SignedHours: View {
    let hours: Double
    var font: Font = BrianType.number

    var body: some View {
        let sign = hours > 0 ? "+" : (hours < 0 ? "−" : "")
        Text("\(sign)\(abs(hours), specifier: "%.1f") h")
            .font(font)
            .foregroundStyle(hours > 0 ? Brian.earn : (hours < 0 ? Brian.cost : Brian.ink))
            .accessibilityLabel(hours >= 0 ? "plus \(abs(hours), specifier: "%.1f") hours" : "minus \(abs(hours), specifier: "%.1f") hours")
    }
}
```

## Layout rules

- Screen background is `Brian.page`. Panels are `Brian.surface` with `Brian.panelRadius`.
  Rows inside a panel are white tiles (`Brian.page`, `Brian.tileRadius`) or a plain `List`.
- Spacing: 4 / 8 / 16 / 24 / 32. Section gaps 24. Panel padding 20.
- One hero number per screen (Today only). Label under it in `BrianType.secondary`,
  `Brian.muted`: "healthy‑life hours today".
- Lists: leading SF Symbol 18 pt `.secondary`, title in `.body`, detail in
  `.subheadline` muted, trailing outcome chip. Chips are capsules, 12 pt text, fill
  `surface2` (neutral), `earnSoft` (earned), `costSoft` (cost). Never green/red text in
  body copy.
- Toolbar and the primary button use Liquid Glass (`.glass`, `.glassProminent`). Content
  surfaces never use glass. Never glass on glass.
- Icons: SF Symbols only. Trigger families map to exactly these symbols:
  food `fork.knife`, caffeine `cup.and.saucer.fill`, alcohol `wineglass.fill`,
  outdoor `sun.max.fill`, screen `display`, people `person.2.fill`, biometric
  `heart.fill`, medication `pills.fill`, wind‑down `moon.fill`, walk `figure.walk`,
  glasses `eyeglasses`, backend `desktopcomputer`, watching `record.circle`.
  Layers, matching the dashboard's lucide set one for one: Clock `clock`, Light
  `sun.max`, People `person.2`, Outside `tree`, Air `wind`, Mind `brain`, Body `figure.walk`.
  Outcomes: said `waveform`, asked `questionmark.bubble`, acted `checkmark.seal.fill`,
  held back: no icon, muted words "held back".

## Copy rules

Sentence case. Plain words a tired person reads in one pass. Numbers, not adjectives.
Say what happened, not what the system "thinks". Verbs on buttons: "Start watching",
"Stop", "Try again", "Open Meta AI", "Allow", "Undo", "Mark done", "Add item".
Status lines are noun + state: "Glasses connected", "Backend 10.0.0.5", "Watching 14 min".
Errors: one sentence of cause, one button of fix.
Empty states are instructions, not consolation. Today, nothing yet: "Put the glasses on.
Bryan starts counting light, people, and air the moment the camera is up." (voice.md's
exact string) with the button right under it. Never "No data available", never an
illustration.
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
    `brian-ui/references/voice.md` (spoke, silent, demo, N/A, an exclamation mark) scores
    this item 0.

A screen ships at ≥ 16/20 with no item scored 0.
