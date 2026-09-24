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

    @Test func eachScreenLandsOnItsTabAndSheetsOnToday() {
        #expect(AppScreen.today.tab == .today)
        #expect(AppScreen.calendar.tab == .calendar)
        #expect(AppScreen.analysis.tab == .analysis)
        #expect(AppScreen.protocol.tab == .protocol)
        #expect(AppScreen.connect.tab == .today)
        #expect(AppScreen.settings.tab == .today)
    }

    @Test func tabsAreInPRDOrder() {
        #expect(AppTab.allCases == [.today, .calendar, .analysis, .protocol])
    }
}
