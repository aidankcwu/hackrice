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
            "tone": ["warm"],
            "people": ["matters"]
        ]
    }

    /// Ungrammatical they/them pairs the fixed composer must never produce.
    private let badTheyPairs = [
        "They is", "They has", "They does", "They usually falls",
        "They skips", "They works", "They tends", "They eats", "They wants"
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
            "days": ["student", "desk", "feet", "mixed"],
            "bedtime": ["early", "late", "verylate", "varies"],
            "training": ["none", "casual", "event"],
            "food": ["regular", "skip", "desk", "night"],
            "caffeine": ["none", "keep", "less", "noon"],
            "frequency": ["rarely", "moments", "often"]
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
}
