// The `-screen <id>` launch argument that screenshots rely on.
import Testing
@testable import Brian

struct AppScreenTests {
    @Test func readsTheValueAfterTheArgument() {
        #expect(AppScreen.from(arguments: ["Zeroist", "-demo", "-screen", "protocol"]) == .protocol)
        #expect(AppScreen.from(arguments: ["-screen", "Connect"]) == .connect)
        #expect(AppScreen.from(arguments: ["-screen", "preview"]) == .preview)
        #expect(AppScreen.from(arguments: ["-screen", "protocol-edit"]) == .protocolEdit)
        #expect(AppScreen.from(arguments: ["-screen", "protocol-templates"]) == .protocolTemplates)
        #expect(AppScreen.from(arguments: ["-screen", "session"]) == .session)
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
        #expect(AppScreen.protocolTemplates.tab == .protocol)
        #expect(AppScreen.protocolEdit.tab == .protocol)
        #expect(AppScreen.session.tab == .home)
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
        #expect(HomeScrollTarget("protocol")?.section == .protocol)
        #expect(HomeScrollTarget("sessions")?.section == .sessions)
        #expect(HomeScrollTarget("nowhere") == nil)
        #expect(HomeScrollTarget("-end") == nil)
    }
}
