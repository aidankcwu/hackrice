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
    /// Picks allowed: 1 is single choice (tapping advances), 0 is any number, >1 caps a
    /// multi choice. Anything but 1 continues with a button.
    let maxPicks: Int
    /// A multi-choice option that clears the others when picked ("None"), or nil.
    let exclusiveOptionID: String?
    /// An option that opens a text field for the wearer's own words ("Other"), or nil.
    /// The text is stored under `textKey`; picking it never auto-advances.
    var freeTextOptionID: String? = nil
    var freeTextPlaceholder: String = "Type it here"

    var isMulti: Bool { maxPicks != 1 }
    var isUnlimited: Bool { maxPicks == 0 }
    /// Where the free text for this question is stored in `PersonaAnswers`.
    var textKey: String { "\(id).text" }
}

/// Question id → picked option ids, in the order shown. A question's free text, when
/// it has one, is stored as a single-element array under `question.textKey`.
typealias PersonaAnswers = [String: [String]]

extension PersonaAnswers {
    /// The wearer's own words for a question, trimmed and cut to one short line, or nil.
    func freeText(for question: PersonaQuestion) -> String? {
        PersonaAnswers.cleanFreeText(self[question.textKey]?.first)
    }

    static func cleanFreeText(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let oneLine = raw.replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !oneLine.isEmpty else { return nil }
        return String(oneLine.prefix(40))
    }
}
