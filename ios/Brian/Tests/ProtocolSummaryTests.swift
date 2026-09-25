// DEMO_UI_PRD.md "Home · 3. Protocol card" and "Protocol tab": the count, the order, and
// the state word of every item at a given time of day.
import Foundation
import Testing
@testable import Brian

struct ProtocolSummaryTests {
    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let us = Locale(identifier: "en_US")

    private func at(_ hour: Int, _ minute: Int = 0) -> Date {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = chicago
        return c.date(from: DateComponents(year: 2026, month: 9, day: 22, hour: hour, minute: minute))!
    }

    private func item(_ id: String, _ start: String, _ end: String, status: String = "waiting",
                      seenAt: Double? = nil, kind: String = "dose") -> ProtocolItem {
        ProtocolItem(id: id, name: id, kind: kind, windowStart: start, windowEnd: end,
                     days: Array(0...6), status: status, seenAt: seenAt)
    }

    private func summary(_ items: [ProtocolItem], now: Date) -> ProtocolSummary {
        ProtocolSummary.derive(items: items, now: now, locale: us, timeZone: chicago)
    }

    private func state(_ item: ProtocolItem, now: Date) -> ProtocolSummary.Row {
        summary([item], now: now).rows[0]
    }

    /// ICU puts a narrow no-break space before AM / PM (and thin spaces round a range dash).
    private func plain(_ text: String?) -> String? {
        text.map { String($0.map { ["\u{202F}", "\u{2009}", "\u{00A0}"].contains($0) ? " " : $0 }) }
    }

    @Test func seenCarriesTheTimeAndAFilledCheck() {
        let seen = at(8, 42).timeIntervalSince1970
        let row = state(item("a", "07:00", "10:00", status: "seen", seenAt: seen), now: at(12))
        #expect(plain(row.state) == "Seen 8:42 AM")
        #expect(row.stateSymbol == "checkmark.circle.fill")
        #expect(row.tone == .complete)
        #expect(row.checked)
    }

    @Test func seenWithoutATimeIsJustSeen() {
        #expect(plain(state(item("a", "07:00", "10:00", status: "seen"), now: at(12)).state) == "Seen")
    }

    @Test func doneIsAnOutlinedCheck() {
        let row = state(item("a", "07:00", "10:00", status: "done"), now: at(8))
        #expect(row.state == "Done")
        #expect(row.stateSymbol == "checkmark.circle")
        #expect(row.checked)
    }

    @Test func missedFromTheBackendIsCostWithACross() {
        let row = state(item("a", "07:00", "10:00", status: "missed"), now: at(8))
        #expect(row.state == "Missed")
        #expect(row.stateSymbol == "xmark.circle")
        #expect(row.tone == .missed)
        #expect(!row.checked)
    }

    @Test func waitingBeforeTheWindowIsLater() {
        let row = state(item("a", "19:00", "22:00"), now: at(18, 59))
        #expect(row.state == "Later")
        #expect(row.stateSymbol == nil)
        #expect(row.tone == .pending)
    }

    @Test func waitingInsideTheWindowIsOpenUntilItsEnd() {
        #expect(plain(state(item("a", "19:00", "22:00"), now: at(19)).state) == "Open until 10 PM")
        #expect(plain(state(item("a", "21:30", "23:30"), now: at(22)).state) == "Open until 11:30 PM")
    }

    @Test func waitingAfterTheWindowClosedIsMissed() {
        let row = state(item("a", "07:00", "10:00"), now: at(10))
        #expect(row.state == "Missed")
        #expect(row.tone == .missed)
    }

    @Test func undoneReadsLikeWaiting() {
        #expect(plain(state(item("a", "07:00", "10:00", status: "undone"), now: at(9)).state) == "Open until 10 AM")
        #expect(!state(item("a", "07:00", "10:00", status: "undone"), now: at(9)).checked)
    }

    @Test func aWindowTheBackendMangledIsLaterNotACrash() {
        #expect(plain(state(item("a", "", "bad"), now: at(9)).state) == "Later")
    }

    @Test func rowsAreInWindowOrderAndCountSeenAndDone() {
        let s = summary([
            item("evening", "19:00", "22:00"),
            item("morning", "07:00", "10:00", status: "seen"),
            item("lunch", "11:30", "14:00", status: "done"),
            item("walk", "07:00", "16:00", status: "missed"),
        ], now: at(12))
        #expect(s.rows.map(\.id) == ["morning", "walk", "lunch", "evening"])
        #expect(s.completed == 2)
        #expect(s.count == "2 of 4")
        #expect(!s.isEmpty)
    }

    @Test func noItemsIsEmpty() {
        let s = summary([], now: at(12))
        #expect(s.isEmpty)
        #expect(s.count == "0 of 0")
    }

    @Test func windowTextIsTheLocaleShortRange() {
        let text = plain(ProtocolSummary.windowText(start: 7 * 60, end: 10 * 60, locale: us))
        #expect(text?.contains("7:00") == true)
        #expect(text?.contains("10:00 AM") == true)
        #expect(ProtocolSummary.windowText(start: nil, end: 60, locale: us) == nil)
    }

    @Test func minutesParsesHHMMOnly() {
        #expect(ProtocolSummary.minutes("08:30") == 510)
        #expect(ProtocolSummary.minutes("24:00") == nil)
        #expect(ProtocolSummary.minutes("8") == nil)
    }

    /// The demo fixture at 12:00 in Chicago: morning dose and daylight walk seen, lunch done.
    @MainActor @Test func theFixtureIsThreeOfFive() throws {
        let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))
        let today: ProtocolToday = try api.fixture("protocol_today")
        let s = summary(today.items, now: at(12))
        #expect(s.count == "3 of 5")
        #expect(s.rows.map { plain($0.state)! } == ["Seen 8:42 AM", "Seen 3:29 PM", "Done", "Later", "Later"])
        #expect(plain(summary(today.items, now: at(19, 30)).rows[3].state) == "Open until 10 PM")
    }
}

struct ProtocolTemplatesTests {
    @Test func sixGroupsOfAtLeastThreeInTheWebOrder() {
        #expect(ProtocolTemplates.groups.map(\.title) == ["Doses", "Meals", "Movement", "Light", "Sleep", "Screens"])
        #expect(ProtocolTemplates.groups.allSatisfy { $0.templates.count >= 3 })
    }

    @Test func everyTemplateIsSomethingTheBackendAccepts() {
        let kinds = Set(ProtocolTemplates.kinds.map(\.value))
        #expect(kinds == ["dose", "meal", "walk", "winddown"])
        for template in ProtocolTemplates.groups.flatMap(\.templates) {
            #expect(kinds.contains(template.kind), "\(template.name)")
            let start = ProtocolSummary.minutes(template.windowStart) ?? .max
            let end = ProtocolSummary.minutes(template.windowEnd) ?? .min
            #expect(start < end, "\(template.name)")
            #expect(!template.days.isEmpty && template.days.allSatisfy { (0...6).contains($0) })
        }
    }

    @Test func addedMatchesByNameIgnoringCase() {
        let template = ProtocolTemplate(name: "Morning dose", kind: "dose", windowStart: "07:00", windowEnd: "10:00")
        let items = [ProtocolItem(id: "x", name: " morning DOSE", kind: "dose", windowStart: "07:00",
                                  windowEnd: "10:00", days: [0], status: "waiting")]
        #expect(ProtocolTemplates.isAdded(template, in: items))
        #expect(!ProtocolTemplates.isAdded(template, in: []))
    }

    @Test func daysTextNamesOnlyPartialWeeks() {
        #expect(ProtocolTemplate(name: "a", kind: "dose", windowStart: "07:00", windowEnd: "08:00").daysText == nil)
        #expect(ProtocolTemplate(name: "a", kind: "dose", windowStart: "07:00", windowEnd: "08:00",
                                 days: [3, 0]).daysText == "Mondays, Thursdays")
    }
}

/// Fixture mode's protocol edits: what the checkbox and the edit sheet change.
@MainActor
struct ProtocolFixtureEditTests {
    private func api() -> APIClient { APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self)) }

    @Test func editKeepsTodaysStatusAndReordersByWindow() async throws {
        let api = api()
        try await api.updateProtocol(id: "pi_1a2b3c4d", name: "Late dose", kind: "dose",
                                     windowStart: "23:00", windowEnd: "23:30", days: [2, 0, 2])
        let items = try await api.protocolToday()
        let edited = try #require(items.last)
        #expect(edited.id == "pi_1a2b3c4d")
        #expect(edited.name == "Late dose")
        #expect(edited.days == [0, 2])
        #expect(edited.status == "seen")
    }

    @Test func checkboxRoundTrip() async throws {
        let api = api()
        try await api.protocolDone(id: "pi_3a4b5c6d")
        #expect(try await api.protocolToday().first { $0.id == "pi_3a4b5c6d" }?.status == "done")
        try await api.protocolUndo(id: "pi_3a4b5c6d")
        #expect(try await api.protocolToday().first { $0.id == "pi_3a4b5c6d" }?.status == "undone")
    }

    @Test func aTemplateAddsAsAWaitingItem() async throws {
        let api = api()
        let template = ProtocolTemplates.groups[0].templates[2]
        try await api.addProtocol(name: template.name, kind: template.kind, windowStart: template.windowStart,
                                  windowEnd: template.windowEnd, days: template.days)
        let items = try await api.protocolToday()
        #expect(items.count == 6)
        #expect(ProtocolTemplates.isAdded(template, in: items))
        #expect(items.first?.name == template.name)   // 5:35 AM sorts first
    }
}
