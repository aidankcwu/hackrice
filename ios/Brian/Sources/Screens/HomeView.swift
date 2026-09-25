// DEMO_UI_PRD.md "Home". Start watching / Stop lives in the shared header now. Metrics
// (hero, Daylight / Screens tiles, watched line) are D-003; the summary, protocol card,
// sessions, stats and log arrive in D-004 … D-008.
import SwiftUI

struct HomeView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize
    /// Opens "How this is measured"; RootView owns the sheet.
    var openMeasured: () -> Void = {}

    /// Side by side at normal sizes; stacked at accessibility sizes, where a button or chip
    /// beside a sentence squeezes it to one word per line.
    private func row(spacing: CGFloat, alignment: VerticalAlignment = .center) -> AnyLayout {
        typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: alignment, spacing: spacing))
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.section) {
                if let error = appState.lastError { errorRow(error) }
                metrics
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
    }

    /// Connection problems live in the status pill; this is everything else that failed
    /// (a refresh, a start): one sentence of cause, one button of fix.
    private func errorRow(_ error: String) -> some View {
        let layout = row(spacing: 16)
        return layout {
            Text(error)
                .font(BrianType.secondary)
                .foregroundStyle(Brian.cost)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("Try again") { Task { await appState.refreshToday() } }
                .buttonStyle(.glass)
        }
        .frame(minHeight: 44)
    }

    /// Hero, the two tiles and the watched line (D-003). Minutes tick with the clock.
    private var metrics: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let values = appState.homeMetrics(now: context.date)
            VStack(alignment: .leading, spacing: 16) {
                hero
                let tiles = typeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(spacing: 16))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 16))
                tiles {
                    tile(value: values.daylight, label: "Daylight", symbol: BrianSymbol.family(HomeMetrics.daylightKind))
                    tile(value: values.screens, label: "Screens", symbol: BrianSymbol.family(HomeMetrics.screensKind))
                }
                Text(values.watchedLine)
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
            }
        }
    }

    private var hero: some View {
        Button(action: openMeasured) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    SignedHours(hours: appState.healthspan?.hoursToday ?? 0, font: BrianType.hero)
                        .tracking(BrianType.heroTracking)
                        .minimumScaleFactor(0.65)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Image(systemName: "info.circle")
                        .font(.body)
                        .foregroundStyle(Brian.muted)
                        .accessibilityHidden(true)
                }

                let label = row(spacing: 8, alignment: .firstTextBaseline)
                label {
                    Text(Hours.word(appState.healthspan?.hoursToday ?? 0))
                        .font(BrianType.secondary)
                        .foregroundStyle(Brian.muted)
                    Chip(text: provenance)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .panel()
            .contentShape(RoundedRectangle(cornerRadius: Brian.panelRadius, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityHint("Shows how this is measured")
    }

    /// A small panel: the value, then its label. "—" when nothing of that kind was seen.
    private func tile(value: String?, label: String, symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value ?? "—")
                .font(BrianType.number)
                .foregroundStyle(value == nil ? Brian.muted : Brian.ink)
            Label {
                Text(label)
            } icon: {
                Image(systemName: symbol)
            }
            .font(BrianType.secondary)
            .foregroundStyle(Brian.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Brian.surface, in: RoundedRectangle(cornerRadius: Brian.panelRadius, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value ?? "none seen")")
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
