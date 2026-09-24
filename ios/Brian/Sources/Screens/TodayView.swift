import SwiftUI

struct TodayView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSetup = false
    @State private var askConsent = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.section) {
                StatusStrip { showSetup = true }

                Button(appState.watching ? "Stop" : "Start watching") {
                    if appState.watching {
                        Task { await appState.stopWatching() }
                    } else if appState.consentGiven || StreamingConsent.isGranted {
                        appState.consentGiven = true
                        Task { await appState.startWatching() }
                    } else {
                        askConsent = true
                    }
                }
                .buttonStyle(.glassProminent)
                .controlSize(.large)
                .frame(maxWidth: .infinity)

                hero
                ledger
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, 24)
        }
        .background(Brian.page)
        .refreshable { await appState.refreshToday() }
        .task { await appState.refreshToday() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await appState.refreshToday() } }
        }
        .sheet(isPresented: $showSetup) { SetupView() }
        .streamingConsentSheet(isPresented: $askConsent) {
            appState.consentGiven = true
            Task { await appState.startWatching() }
        }
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 8) {
            SignedHours(hours: appState.healthspan?.hoursToday ?? 0, font: BrianType.hero)
                .tracking(BrianType.heroTracking)
                .minimumScaleFactor(0.65)
                .lineLimit(1)

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(Hours.word(appState.healthspan?.hoursToday ?? 0))
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
                Text(provenance)
                    .font(BrianType.chip)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Brian.surface2, in: Capsule())
            }

            Text(scoreLine)
                .font(BrianType.secondary)
                .foregroundStyle(Brian.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panel()
    }

    private var ledger: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Today")
                .font(BrianType.title)
                .padding(.bottom, 8)

            if entries.isEmpty {
                Text("Put the glasses on. Bryan starts counting light, people, and air the moment the camera is up.")
                    .font(BrianType.body)
                    .foregroundStyle(Brian.muted)
                    .padding(.vertical, 16)
            } else {
                ForEach(Array(entries.enumerated()), id: \.element.id) { index, entry in
                    if let decision = entry.decision {
                        NavigationLink {
                            DecisionDetailView(decision: decision, reported: entry.reported)
                        } label: {
                            LedgerRow(entry: entry)
                        }
                        .buttonStyle(.plain)
                    } else {
                        LedgerRow(entry: entry)
                    }
                    if index < entries.count - 1 { Divider().overlay(Brian.line) }
                }
            }

            Text("Held back \(heldBackCount) today")
                .font(BrianType.caption)
                .foregroundStyle(Brian.muted)
                .padding(.top, 16)
        }
    }

    private var provenance: String {
        guard let healthspan = appState.healthspan else { return "Unmeasured" }
        if appState.demo || healthspan.measured == false || healthspan.provenance?.lowercased() == "seeded" { return "Seeded" }
        switch healthspan.provenance?.lowercased() {
        case "glasses": return "Glasses"
        case "whoop": return "WHOOP"
        case "health": return "Health"
        default: return "Unmeasured"
        }
    }

    private var scoreLine: String {
        guard let value = appState.healthspan else { return "Score unmeasured" }
        let score = Int(value.overall.rounded())
        guard let years = value.yearsDelta else { return "Score \(score)" }
        // Same zero guard as Theme.SignedHours: decide the sign from the magnitude that
        // will actually print, not the raw (possibly -0.0, or just-off-zero) sign of
        // `years`, so this line can never read "· −0.0 years".
        let magnitude = String(format: "%.1f", abs(years))
        let sign = magnitude == "0.0" ? "" : (years > 0 ? "+" : "−")
        return "Score \(score) · \(sign)\(magnitude) years"
    }

    private var entries: [LedgerEntry] {
        var result: [LedgerEntry] = []
        var usedEpisodeIDs = Set<String>()

        for decision in appState.decisions {
            let episode = decision.episodeId.flatMap { id in appState.episodes.first { $0.id == id } }
            if let id = decision.episodeId { usedEpisodeIDs.insert(id) }
            let chip = outcome(for: decision)
            // A no-op decision (only annotate/log_insight/watch/nothing/remember, no
            // episode to anchor it) is noise, not a moment worth a row — drop it. Keep
            // every row that has an episode behind it or a chip to show.
            guard episode != nil || chip != nil else { continue }
            result.append(LedgerEntry(
                id: "decision-\(decision.id)", date: Date(timeIntervalSince1970: decision.t),
                label: label(for: decision, episode: episode), kind: episode?.kind ?? decision.trigger,
                outcome: chip, decision: decision, reported: episode?.reported))
        }
        for episode in appState.episodes where !usedEpisodeIDs.contains(episode.id) {
            result.append(LedgerEntry(
                id: "episode-\(episode.id)", date: Date(timeIntervalSince1970: episode.startT),
                label: episode.label ?? cleanedTrigger(episode.kind), kind: episode.kind, outcome: nil,
                decision: nil, reported: episode.reported))
        }
        return result.sorted { $0.date > $1.date }
    }

    /// Human row label: the episode's own label first, then the decision's plain-English
    /// interpretation, and only as a last resort a cleaned-up trigger. Raw triggers such
    /// as "watch:check for more caffeine" must never reach the row directly (brian-ui law).
    private func label(for decision: Decision, episode: Episode?) -> String {
        if let label = episode?.label, !label.isEmpty { return label }
        if !decision.interpretation.isEmpty { return decision.interpretation }
        return cleanedTrigger(decision.trigger)
    }

    /// Strips the internal "watch:" / "autopilot:" scheduling prefixes and turns
    /// snake_case into words, so a raw trigger never leaks as a colon-prefixed key.
    private func cleanedTrigger(_ trigger: String) -> String {
        var t = trigger
        for prefix in ["watch:", "autopilot:"] where t.hasPrefix(prefix) {
            t.removeFirst(prefix.count)
        }
        return t.replacingOccurrences(of: "_", with: " ")
    }

    /// Outcome badge, or nil for no chip at all.
    ///
    /// - `said`: spoke == true, or a proposed ask/speak that was handed off to a
    ///   conversation.
    /// - `asked`: an `ask` action still pending (not handed off) — e.g. outcome
    ///   `conversation_active`.
    /// - `acted`: a real `act` action (a calendar block, a screen shield, ...).
    /// - `held back` (brian-ui law 4): restraint, not silence — an ask or speak action
    ///   that was proposed and then suppressed (`Decision.heldBack`), and isn't already
    ///   covered by `asked` above.
    /// - nil: the decision's actions are only bookkeeping (annotate/log_insight/watch/
    ///   nothing/remember) — nothing was proposed, so there is nothing to show restraint
    ///   about.
    private func outcome(for decision: Decision) -> LedgerEntry.Outcome? {
        if decision.spoke { return .said }
        if decision.actions.contains(where: { $0.proposesSpeech && $0.handedOff }) { return .said }
        if decision.actions.contains(where: { $0.type == "ask" }) { return .asked }
        if decision.actions.contains(where: { $0.type == "act" }) { return .acted }
        if decision.heldBack { return .heldBack }
        return nil
    }

    /// Footer count — the same rule as the row chips, not AppState's own tally, so the
    /// number on screen always matches what "held back" rows are actually visible above.
    private var heldBackCount: Int {
        appState.decisions.filter { outcome(for: $0) == .heldBack }.count
    }
}
