// Tokens from .claude/skills/brian-ios-design/SKILL.md ("Tokens", F.0: direction C ·
// Instrument, A's green/red pair). The first half of this file is the skill's block
// verbatim; below "Helpers" are the chip, symbol and font helpers the skill's layout
// rules describe, so Screens use them by name instead of re-deriving them.
import SwiftUI
import UIKit

/// Bryan palette: direction C · Instrument, picked in F.0, with A's green/red pair for the sign.
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
    static let timeColumn: CGFloat = 64   // ledger time column, wide enough for "12:00 PM" on one line
                                           // at default Dynamic Type; then 8 · symbol 18 · 16 · title · 8 · outcome
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
        let magnitude = String(format: "%.1f", abs(r))
        // The sign is decided from the *displayed* magnitude, not the raw sign of `r`:
        // r can be -0.0 (or a hair off zero from the *10/÷10 round-trip) for any hours
        // value that rounds to zero, and -0.0 < 0 is false but a naive `r < 0` on the
        // pre-division value, or float noise, must never slip a "−" in front of "0.0 h".
        let isZero = magnitude == "0.0"
        let isNegative = !isZero && r < 0
        let isPositive = !isZero && r > 0
        let sign = isPositive ? "+" : (isNegative ? "−" : "")
        Text(sign + magnitude + " h")
            .font(font)
            .foregroundStyle(isPositive ? Brian.earn : (isNegative ? Brian.cost : Brian.ink))
            .accessibilityLabel(isPositive ? "plus \(magnitude) hours" : (isNegative ? "minus \(magnitude) hours" : "\(magnitude) hours"))
    }
}

// MARK: - Helpers (Coder A, from the skill's layout rules)

extension Font {
    /// `SignedHours(hours: h, font: .hero)` as IOS_SPEC.md writes it. Pair with
    /// `.tracking(BrianType.heroTracking)`.
    static var hero: Font { BrianType.hero }
}

/// Capsule chip: 12 pt `BrianType.chip`. For provenance and signed values only
/// (skill, layout rules). Neutral = `surface2`, gain = `earnSoft`, cost = `costSoft`.
struct Chip: View {
    enum Tone { case neutral, earn, cost }

    let text: String
    var tone: Tone = .neutral

    var body: some View {
        Text(text)
            .font(BrianType.chip)
            .foregroundStyle(foreground)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(background, in: Capsule())
    }

    private var foreground: Color {
        switch tone {
        case .neutral: Brian.text
        case .earn: Brian.earn
        case .cost: Brian.cost
        }
    }

    private var background: Color {
        switch tone {
        case .neutral: Brian.surface2
        case .earn: Brian.earnSoft
        case .cost: Brian.costSoft
        }
    }
}

/// The hero's provenance capsule: Glasses · WHOOP · Health · Seeded, from
/// `Healthspan.provenance`. Always neutral; it states a source, not a value.
struct ProvenanceChip: View {
    let provenance: String?

    var body: some View {
        Chip(text: Self.word(provenance), tone: .neutral)
            .accessibilityLabel("Source: \(Self.word(provenance))")
    }

    static func word(_ provenance: String?) -> String {
        switch provenance?.lowercased() {
        case "glasses": "Glasses"
        case "whoop": "WHOOP"
        case "health": "Health"
        default: "Seeded"
        }
    }
}

/// A signed-hours capsule: "+0.4 h" on `earnSoft`, "−0.2 h" on `costSoft`.
struct SignedHoursChip: View {
    let hours: Double

    var body: some View {
        let r = Hours.rounded(hours)
        // Same zero guard as SignedHours: judge "is this a zero chip" from the string
        // that will actually be shown, not the raw (possibly -0.0) sign of `r`.
        let isZero = String(format: "%.1f", abs(r)) == "0.0"
        let tone = isZero ? Brian.surface2 : (r < 0 ? Brian.costSoft : Brian.earnSoft)
        SignedHours(hours: hours, font: BrianType.chip)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(tone, in: Capsule())
    }
}

/// SF Symbols, exactly the skill's mapping. Nothing else is an icon.
enum BrianSymbol {
    static let glasses = "eyeglasses"
    static let backend = "desktopcomputer"
    static let watching = "record.circle"
    static let settings = "gearshape"
    // Outcomes. Held back has no icon: muted words "held back".
    static let whispered = "waveform"
    static let asked = "questionmark.bubble"
    static let acted = "checkmark.seal.fill"

    /// A trigger (`caffeine_seen`, `watch:still at screen?`, `autopilot:outdoor`) or an
    /// episode kind (`caffeine_sighting`, `outdoor_block`) to its family symbol.
    static func family(_ key: String) -> String {
        let k = key.lowercased()
        if k.contains("caffeine") || k.contains("coffee") { return "cup.and.saucer.fill" }
        if k.contains("alcohol") { return "wineglass.fill" }
        if k.contains("food") || k.contains("meal") { return "fork.knife" }
        if k.contains("outdoor") || k.contains("daylight") { return "sun.max.fill" }
        if k.contains("screen") { return "display" }
        if k.contains("people") || k.contains("conversation") || k.contains("social") { return "person.2.fill" }
        if k.contains("biometric") || k.contains("heart") { return "heart.fill" }
        if k.contains("medication") || k.contains("dose") || k.contains("pill") { return "pills.fill" }
        if k.contains("wind") || k.contains("sleep") { return "moon.fill" }
        if k.contains("walk") { return "figure.walk" }
        return "eyeglasses"
    }

    /// Protocol kinds (backend PROTOCOL_KINDS: dose, meal, winddown, walk).
    static func protocolKind(_ kind: String) -> String {
        switch kind {
        case "dose": "pills.fill"
        case "meal": "fork.knife"
        case "winddown", "sleep": "moon.fill"
        case "walk": "figure.walk"
        default: "pills.fill"
        }
    }
}
