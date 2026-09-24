// The `-screen <id>` launch argument that screenshots rely on.
import Testing
@testable import Brian

struct AppScreenTests {
    @Test func readsTheValueAfterTheArgument() {
        #expect(AppScreen.from(arguments: ["Zeroist", "-demo", "-screen", "protocol"]) == .protocol)
        #expect(AppScreen.from(arguments: ["-screen", "Connect"]) == .connect)
    }

    @Test func missingOrUnknownIsNil() {
        #expect(AppScreen.from(arguments: ["-demo"]) == nil)
        #expect(AppScreen.from(arguments: ["-screen"]) == nil)
        #expect(AppScreen.from(arguments: ["-screen", "home"]) == nil)
    }

    @Test func sheetsAndPendingTabsLandOnToday() {
        #expect(AppScreen.connect.tab == .today)
        #expect(AppScreen.settings.tab == .today)
        #expect(AppScreen.protocol.tab == .protocol)
    }
}
