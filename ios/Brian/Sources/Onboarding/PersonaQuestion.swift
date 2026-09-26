// First-launch questionnaire: the shape every question takes. The questions themselves
// (`PersonaQuestion.all`) and the prose they compose into live beside this file; the
// answers only ever feed the persona text, never the protocol.
import Foundation

/// One tappable answer. `id` is stable (stored answers and the composer key on it).
struct PersonaOption: Identifiable, Hashable, Sendable {
    let id: String
    let label: String
}

/// One screen of the questionnaire.
struct PersonaQuestion: Identifiable, Hashable, Sendable {
    let id: String
    let prompt: String
    /// One short line under the prompt, or nil.
    let detail: String?
    let options: [PersonaOption]
    /// Picks allowed: 1 is single choice (tapping advances), >1 is multi choice (Continue).
    let maxPicks: Int
    /// A multi-choice option that clears the others when picked ("None"), or nil.
    let exclusiveOptionID: String?

    var isMulti: Bool { maxPicks > 1 }
}

/// Question id → picked option ids, in the order shown.
typealias PersonaAnswers = [String: [String]]
