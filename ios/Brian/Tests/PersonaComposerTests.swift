import XCTest
@testable import Brian

final class PersonaComposerTests: XCTestCase {
    /// Every question answered once, so every composer branch fires. `pronoun` is
    /// injected as the "pronouns" answer; other fields are fixed so the fixtures
    /// below (feet/his, often/him) are exercisable for any pronoun.
    private func fullAnswers(pronoun: String) -> PersonaAnswers {
        [
            "pronouns": [pronoun],
            "days": ["feet"],
            "goals": ["sleep", "daylight", "meals"],
            "bedtime": ["late"],
            "training": ["casual"],
            "food": ["skip"],
            "caffeine": ["noon"],
            "cut": ["alcohol", "nicotine"],
            "frequency": ["often"],
            "quiet": ["morning", "driving"],
            "tone": ["warm"],
            "people": ["matters"]
        ]
    }

    /// Ungrammatical they/them pairs the fixed composer must never produce.
    private let badTheyPairs = [
        "They is", "They has", "They does", "They usually falls",
        "They skips", "They works", "They tends", "They eats", "They wants",
        "They keeps", "They cooks", "They snacks", "They moves", "They is"
    ]

    func testQuestionAndOptionIDsAreUniqueAndHaveChoices() {
        XCTAssertEqual(Set(PersonaQuestion.all.map(\.id)).count, PersonaQuestion.all.count)
        for question in PersonaQuestion.all {
            XCTAssertGreaterThanOrEqual(question.options.count, 2)
            XCTAssertEqual(Set(question.options.map(\.id)).count, question.options.count)
        }
    }

    func testPronounAgreement() {
        XCTAssertTrue(PersonaComposer.compose(["pronouns": ["he"], "goals": ["sleep"]]).contains("He wants"))
        XCTAssertTrue(PersonaComposer.compose(["pronouns": ["she"], "goals": ["sleep"]]).contains("She wants"))
        XCTAssertTrue(PersonaComposer.compose(["pronouns": ["they"], "goals": ["sleep"]]).contains("They want"))
    }

    func testNoonCaffeineNamesTheCutoff() {
        let text = PersonaComposer.compose(["caffeine": ["noon"]])
        XCTAssertTrue(text.lowercased().contains("noon"))
        XCTAssertTrue(text.lowercased().contains("cutoff"))
    }

    func testEmptyAnswersRemainUsefulAndGuardrailsAreAlwaysPresent() {
        let text = PersonaComposer.compose([:])
        XCTAssertTrue(text.hasPrefix("The wearer"))
        XCTAssertTrue(text.contains("Say a thing once."))
        XCTAssertTrue(text.contains("no medical advice"))
        XCTAssertTrue(text.contains("calories or macros"))
    }

    func testEmptyAnswersUseTheyThemWithCorrectAgreement() {
        let text = PersonaComposer.compose([:])
        XCTAssertTrue(text.contains("they/them"))
        for bad in badTheyPairs {
            XCTAssertFalse(text.contains(bad), "found ungrammatical pair '\(bad)' in: \(text)")
        }
    }

    func testFullAnswerSetHeAgreement() {
        let text = PersonaComposer.compose(fullAnswers(pronoun: "he"))
        XCTAssertTrue(text.contains("hold him to it"), text)
        XCTAssertTrue(text.contains("on his feet"), text)
    }

    func testFullAnswerSetSheAgreement() {
        let text = PersonaComposer.compose(fullAnswers(pronoun: "she"))
        XCTAssertTrue(text.contains("hold her to it"), text)
        XCTAssertTrue(text.contains("on her feet"), text)
    }

    func testFullAnswerSetTheyAgreement() {
        let text = PersonaComposer.compose(fullAnswers(pronoun: "they"))
        for bad in badTheyPairs {
            XCTAssertFalse(text.contains(bad), "found ungrammatical pair '\(bad)' in: \(text)")
        }
    }

    /// Sweeps every option of every verb-bearing question for "they", one at a time,
    /// so each fixed dictionary branch (not just the ones the full fixture happens
    /// to pick) is checked against the ungrammatical pairs.
    func testTheyAgreementAcrossEveryOptionBranch() {
        let optionsByQuestion: [String: [String]] = [
            "days": ["student", "desk", "meetings", "feet", "physical", "driving", "care", "creative", "travel", "home", "mixed"],
            "bedtime": ["early", "late", "verylate", "shift", "varies"],
            "training": ["none", "casual", "routine", "event", "rehab"],
            "food": ["regular", "cook", "out", "skip", "desk", "night"],
            "caffeine": ["none", "keep", "less", "noon", "track"],
            "frequency": ["patterns", "daily", "moments", "often", "every"],
            "quiet": ["morning", "work", "meals", "driving", "evening"],
            "tone": ["dry", "warm", "blunt", "coach", "curious", "light"],
            "people": ["silent", "matters", "brief", "noask"]
        ]
        for (question, options) in optionsByQuestion {
            for option in options {
                let text = PersonaComposer.compose([
                    "pronouns": ["they"],
                    "goals": ["sleep"],
                    question: [option]
                ])
                for bad in badTheyPairs {
                    XCTAssertFalse(text.contains(bad), "\(question)=\(option) produced ungrammatical '\(bad)': \(text)")
                }
            }
        }
    }

    /// Every option id the composer maps must exist in the question list, and vice versa,
    /// so a renamed option cannot silently drop out of the paragraph.
    func testEveryOptionOfEveryQuestionChangesTheParagraph() {
        let base = PersonaComposer.compose([:])
        for question in PersonaQuestion.all {
            for option in question.options {
                var answers: PersonaAnswers = [question.id: [option.id]]
                if option.id == question.freeTextOptionID { answers[question.textKey] = ["ze/zir"] }
                let text = PersonaComposer.compose(answers)
                // "Nothing"/"No quiet times" add nothing by design, and they/them is the default.
                let inert = ["nothing", "never"].contains(option.id) || (question.id == "pronouns" && option.id == "they")
                if !inert {
                    XCTAssertNotEqual(text, base, "\(question.id)=\(option.id) left the paragraph unchanged")
                }
            }
        }
    }

    func testCustomPronounsAreQuotedAndConjugatedSafely() {
        let text = PersonaComposer.compose(["pronouns": ["other"], "pronouns.text": ["ze/zir"],
                                            "goals": ["sleep"], "days": ["feet"], "training": ["casual"]])
        XCTAssertTrue(text.hasPrefix("The wearer uses ze/zir pronouns."), text)
        XCTAssertTrue(text.contains("The wearer wants help"), text)
        XCTAssertTrue(text.contains("on the wearer's feet"), text)
        XCTAssertFalse(text.contains("they/them"), text)
        for bad in badTheyPairs { XCTAssertFalse(text.contains(bad), text) }
    }

    func testCustomPronounsWithoutTextFallBackToTheyThem() {
        let text = PersonaComposer.compose(["pronouns": ["other"], "pronouns.text": ["   "]])
        XCTAssertTrue(text.contains("they/them"), text)
    }

    func testFreeTextIsTrimmedAndCapped() {
        XCTAssertNil(PersonaAnswers.cleanFreeText(nil))
        XCTAssertNil(PersonaAnswers.cleanFreeText(" \n "))
        XCTAssertEqual(PersonaAnswers.cleanFreeText("  xe/xem\n"), "xe/xem")
        XCTAssertEqual(PersonaAnswers.cleanFreeText(String(repeating: "a", count: 80))?.count, 40)
    }

    func testMultiChoiceQuestionsTakeAnyNumberOfPicks() {
        for question in PersonaQuestion.all where question.isMulti {
            XCTAssertTrue(question.isUnlimited, "\(question.id) still caps picks at \(question.maxPicks)")
        }
        XCTAssertNotNil(PersonaQuestion.all.first { $0.id == "pronouns" }?.freeTextOptionID)
    }

    func testTheStandardClosesEveryParagraph() {
        for answers in [[:], fullAnswers(pronoun: "she")] {
            XCTAssertTrue(PersonaComposer.compose(answers).hasSuffix(PersonaComposer.standard))
        }
    }
}
