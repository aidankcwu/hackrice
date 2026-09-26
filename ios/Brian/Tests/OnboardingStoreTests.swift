import XCTest
@testable import Brian

final class OnboardingStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "OnboardingStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testRoundTripAndSyncState() {
        let store = OnboardingStore(defaults: defaults)
        let answers: PersonaAnswers = ["pronouns": ["she"], "goals": ["sleep", "meals"]]
        store.save(answers)
        XCTAssertTrue(store.isComplete)
        XCTAssertEqual(store.answers, answers)
        XCTAssertEqual(store.persona, PersonaComposer.compose(answers))
        XCTAssertTrue(store.needsSync)
        store.markSynced()
        XCTAssertFalse(store.needsSync)
    }

    func testResetClearsEverything() {
        let store = OnboardingStore(defaults: defaults)
        store.save(["pronouns": ["he"]])
        store.reset()
        XCTAssertFalse(store.isComplete)
        XCTAssertEqual(store.answers, [:])
        XCTAssertNil(store.persona)
        XCTAssertFalse(store.needsSync)
    }
}
