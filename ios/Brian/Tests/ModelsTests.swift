// Every fixture decodes with the app's decoder, and the derived fields read right.
import Foundation
import Testing
@testable import Brian

@MainActor
struct ModelsTests {
    let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))

    @Test func healthspanFixtureDecodes() throws {
        let h: Healthspan = try api.fixture("today_healthspan")
        #expect(h.overall == 76)
        #expect(abs(h.hoursToday - (-0.02)) < 1e-9)
        #expect(h.yearsDelta == -0.06)
        #expect(h.day == "2026-09-22")
        #expect(h.measuredCount == 18)
        #expect(h.measuredTotal == 20)
        #expect(h.measured == true)
        // The fixture has live rows from the glasses, so the chip reads Glasses.
        #expect(h.provenance == "glasses")
    }

    @Test func episodesFixtureDecodes() throws {
        let e: [Episode] = try api.fixture("today_episodes")
        #expect(e.count == 38)
        #expect(e.first?.id == "e_1761")
        #expect(e.first?.kind == "screen_block")
        #expect(e.first?.label == "sustained screen time")
        #expect(e.allSatisfy { $0.startT > 1_700_000_000 })
    }

    @Test func decisionsFixtureDecodes() throws {
        let d: [Decision] = try api.fixture("today_decisions")
        #expect(d.count == 50)
        let first = try #require(d.first)
        #expect(first.id == "d_3157")
        #expect(first.trigger == "outdoor_sustained")
        #expect(first.episodeId == "e_1798")
        #expect(first.actions.first?.type == "annotate")
        #expect(first.actions.first?.line == "outdoors")
        #expect(d.filter(\.spoke).count == 8)
        #expect(d.filter(\.heldBack).count == 1)
    }

    @Test func protocolFixtureDecodes() throws {
        let p: ProtocolToday = try api.fixture("protocol_today")
        #expect(p.items.count == 5)
        let dose = try #require(p.items.first)
        #expect(dose.name == "Morning dose")
        #expect(dose.windowStart == "07:00")
        #expect(dose.status == "seen")
        #expect(dose.seenAt != nil)
        #expect(dose.evidenceRef == "d_3121/f_00000412")
        #expect(dose.days == [0, 1, 2, 3, 4, 5, 6])
    }

    @Test func healthspanAllSeededReadsSeeded() throws {
        let json = #"{"overall": 70, "hours_today": 0.4, "measured": {"count": 3, "total": 20}, "provenance": {"steps": {"source": "seeded", "basis": "phone"}, "sri": {"source": "derived", "basis": "whoop"}}}"#
        let h = try decoder().decode(Healthspan.self, from: Data(json.utf8))
        #expect(h.provenance == "seeded")
        #expect(h.yearsDelta == nil)
    }

    @Test func healthspanLiveWhoopReadsWhoop() throws {
        let json = #"{"overall": 70, "hours_today": 0.4, "provenance": {"sri": {"source": "live", "basis": "whoop"}, "steps": {"source": "live", "basis": "apple_watch"}}}"#
        let h = try decoder().decode(Healthspan.self, from: Data(json.utf8))
        #expect(h.provenance == "whoop")
    }

    @Test func oddActionFieldsDoNotBreakTheFeed() throws {
        let json = #"[{"id": "d_1", "t": 1.0, "trigger": "x", "trigger_tick_id": "t_1", "actions": [{"type": "act", "id": "a_1", "kind": "calendar_block", "args": {"minutes": 20}, "outcome": "acted"}, {"type": "watch", "after_s": 900, "reason": "r"}], "spoke": false}]"#
        let d = try decoder().decode([Decision].self, from: Data(json.utf8))
        #expect(d.first?.actions.first?.kind == "calendar_block")
        #expect(d.first?.interpretation == "")
        #expect(d.first?.episodeId == nil)
    }

    @Test func handedOffSpeechIsNotHeldBack() {
        let handed = Decision(id: "a", t: 0, trigger: "x",
                              actions: [DecisionAction(type: "speak", text: "hi", outcome: "handed_off:c_1")])
        let held = Decision(id: "b", t: 0, trigger: "x",
                            actions: [DecisionAction(type: "ask", text: "yours?", outcome: "conversation_active")])
        let spoken = Decision(id: "c", t: 0, trigger: "x", actions: [DecisionAction(type: "speak")], spoke: true)
        let silent = Decision(id: "d", t: 0, trigger: "x", actions: [DecisionAction(type: "annotate")])
        #expect(!handed.heldBack)
        #expect(held.heldBack)
        #expect(!spoken.heldBack)
        #expect(!silent.heldBack)
    }

    @Test func demoAppStateLoadsFixtures() async throws {
        let state = AppState(demo: true)
        await state.refreshToday()
        await state.refreshProtocol()
        #expect(state.lastError == nil)
        #expect(state.healthspan?.provenance == "seeded")
        #expect(state.watching)
        #expect(state.glasses == .connected)
        #expect(state.episodes.count == 38)
        #expect(state.decisions.count == 50)
        #expect(state.heldBackToday == 1)
        #expect(state.protocolItems.count == 5)

        let waiting = try #require(state.protocolItems.first { $0.status == "waiting" })
        await state.markDone(waiting)
        #expect(state.protocolItems.first { $0.id == waiting.id }?.status == "done")
        await state.undo(waiting)
        #expect(state.protocolItems.first { $0.id == waiting.id }?.status == "undone")
        await state.addProtocolItem(name: "Stretch", kind: "walk", windowStart: "17:00", windowEnd: "18:00", days: [0, 2, 4])
        #expect(state.protocolItems.count == 6)
        await state.deleteProtocolItem(waiting)
        #expect(state.protocolItems.count == 5)
    }

    private func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }
}
