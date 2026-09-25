// The `-screen <id>` launch argument that screenshots rely on.
import Testing
@testable import Brian

struct AppScreenTests {
    @Test func readsTheValueAfterTheArgument() {
        #expect(AppScreen.from(arguments: ["Zeroist", "-demo", "-screen", "protocol"]) == .protocol)
        #expect(AppScreen.from(arguments: ["-screen", "Connect"]) == .connect)
        #expect(AppScreen.from(arguments: ["-screen", "preview"]) == .preview)
    }

    @Test func todayIsAnAliasOfHome() {
        #expect(AppScreen.from(arguments: ["-screen", "today"]) == .home)
        #expect(AppScreen.from(arguments: ["-screen", "home"]) == .home)
    }

    @Test func missingOrUnknownIsNil() {
        #expect(AppScreen.from(arguments: ["-demo"]) == nil)
        #expect(AppScreen.from(arguments: ["-screen"]) == nil)
        #expect(AppScreen.from(arguments: ["-screen", "calendar"]) == nil)
    }

    @Test func eachScreenLandsOnItsTabAndSheetsOnHome() {
        #expect(AppScreen.home.tab == .home)
        #expect(AppScreen.analysis.tab == .analysis)
        #expect(AppScreen.protocol.tab == .protocol)
        #expect(AppScreen.connect.tab == .home)
        #expect(AppScreen.settings.tab == .home)
        #expect(AppScreen.preview.tab == .home)
        #expect(AppScreen.measured.tab == .home)
    }

    @Test func threeTabsInPRDOrder() {
        #expect(AppTab.allCases == [.home, .analysis, .protocol])
    }
}

struct HomeScrollTargetTests {
    @Test func parsesSectionsAndTheEndForm() {
        #expect(HomeScrollTarget("summary") == HomeScrollTarget("SUMMARY"))
        #expect(HomeScrollTarget("summary")?.section == .summary)
        #expect(HomeScrollTarget("summary")?.atEnd == false)
        #expect(HomeScrollTarget("summary-end")?.atEnd == true)
        #expect(HomeScrollTarget("log-end")?.section == .log)
        #expect(HomeScrollTarget("nowhere") == nil)
        #expect(HomeScrollTarget("-end") == nil)
    }
}
