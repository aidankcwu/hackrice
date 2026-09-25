// DEMO_UI_PRD.md "Home". Start watching / Stop lives in the shared header now. Metrics
// (hero, Daylight / Screens tiles, watched line) are D-003; the daily summary is D-004; the
// protocol card is D-005; sessions are D-006; the stats sheet is D-007; the log arrives in D-008.
import SwiftUI

/// Home's sections, top to bottom; the ids `-scrollTo` accepts (screenshots).
enum HomeSection: String, CaseIterable {
    case metrics, summary, `protocol`, sessions, stats, log
}

/// `-scrollTo summary` puts the section's top under the header; `summary-end` its bottom
/// above the tab bar, for sections taller than the screen.
struct HomeScrollTarget: Equatable {
    let section: HomeSection
    let atEnd: Bool

    init?(_ argument: String) {
        let value = argument.lowercased()
        atEnd = value.hasSuffix("-end")
        guard let section = HomeSection(rawValue: atEnd ? String(value.dropLast(4)) : value) else { return nil }
        self.section = section
    }
}

struct HomeView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize
    /// Opens "How this is measured"; RootView owns the sheet.
    var openMeasured: () -> Void = {}
    /// Opens the Protocol tab; RootView owns the tab selection.
    var openProtocol: () -> Void = {}
    @State private var sessionsExpanded = false
    /// The session whose detail is pushed.
    @State private var openSession: String?

    /// Side by side at normal sizes; stacked at accessibility sizes, where a button or chip
    /// beside a sentence squeezes it to one word per line.
    private func row(spacing: CGFloat, alignment: VerticalAlignment = .center) -> AnyLayout {
        typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: alignment, spacing: spacing))
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // Not lazy: a few dozen rows, and `-scrollTo` needs real heights to land.
                VStack(alignment: .leading, spacing: Space.section) {
                    if let error = appState.lastError { errorRow(error) }
                    metrics.id(HomeSection.metrics)
                    summary.id(HomeSection.summary)
                    protocolCard.id(HomeSection.protocol)
                    sessionsCard.id(HomeSection.sessions)
                    statsSheet.id(HomeSection.stats)
                    ledger.id(HomeSection.log)
                }
                .padding(.horizontal, Space.gutter)
                .padding(.vertical, 24)
            }
            .task {
                await appState.refreshToday()
                await appState.loadSummaryIfNeeded()
                if let target = appState.homeScrollTarget {
                    appState.homeScrollTarget = nil
                    if target.section == .sessions { sessionsExpanded = true }
                    // Let the page (and expanded rows) lay out first, or the scroll lands short at XXL.
                    try? await Task.sleep(for: .milliseconds(400))
                    proxy.scrollTo(target.section, anchor: target.atEnd ? .bottom : .top)
                }
                openLaunchSession()
            }
        }
        .background(Brian.page)
        .navigationDestination(item: $openSession) { id in
            SessionDetailView(sessionID: id)
        }
        .refreshable { await appState.refreshToday() }
        // The first episode of the day can arrive while Home is open (the 30 s poll).
        .onChange(of: appState.episodes.isEmpty) { _, empty in
            if !empty { Task { await appState.loadSummaryIfNeeded() } }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await appState.refreshToday() } }
        }
        // Bootstrap and this view's task both fetch; the sessions may land after the task.
        .onChange(of: appState.sessions) { _, _ in openLaunchSession() }
    }

    /// Demo `-screen session`: push the newest session once today's sessions are in.
    private func openLaunchSession() {
        guard appState.homeLaunch == .session,
              let newest = appState.sessionsSummary(now: .now).rows.first else { return }
        appState.homeLaunch = nil
        openSession = newest.id
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

    /// Today's written summary (D-004): the backend's recap for midnight → now, as written.
    private var summary: some View {
        let card = appState.summaryCard
        let header = row(spacing: 8, alignment: .firstTextBaseline)
        return VStack(alignment: .leading, spacing: 16) {
            header {
                Text("Today")
                    .font(BrianType.title)
                    .foregroundStyle(Brian.ink)
                    .accessibilityAddTraits(.isHeader)
                    .frame(maxWidth: typeSize.isAccessibilitySize ? nil : .infinity, alignment: .leading)
                if let status = card.status {
                    Text(status)
                        .font(BrianType.secondary)
                        .foregroundStyle(Brian.muted)
                }
            }

            switch card.body {
            case .empty:
                summarySentence(SummaryCard.emptySentence)
            case .writing:
                summarySentence(SummaryCard.writingSentence)
            case .failed:
                summarySentence(SummaryCard.failedSentence)
            case .recap(let recap):
                RecapText(recap: recap)
            }

            if card.showsRefresh {
                Button("Refresh") { Task { await appState.refreshSummary() } }
                    .buttonStyle(.glass)
                    .disabled(appState.summaryLoading)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panel()
    }

    /// Today's protocol at a glance (D-005). Read-only; the whole card opens the Protocol tab.
    private var protocolCard: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let summary = appState.protocolSummary(now: context.date)
            VStack(alignment: .leading, spacing: 16) {
                Text(summary.isEmpty ? "Protocol" : "Protocol · \(summary.count)")
                    .font(BrianType.title)
                    .foregroundStyle(Brian.ink)
                    .accessibilityAddTraits(.isHeader)
                if summary.isEmpty {
                    Text("No protocol yet.")
                        .font(BrianType.body)
                        .foregroundStyle(Brian.muted)
                    Button("Set one up", action: openProtocol)
                        .buttonStyle(.glass)
                } else {
                    Button(action: openProtocol) {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(summary.rows) { protocolRow($0) }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens Protocol")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .panel()
        }
    }

    private func protocolRow(_ item: ProtocolSummary.Row) -> some View {
        let layout = row(spacing: 12, alignment: .firstTextBaseline)
        return layout {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                if !typeSize.isAccessibilitySize {
                    Image(systemName: BrianSymbol.protocolKind(item.kind))
                        .foregroundStyle(Brian.muted)
                        .frame(width: 28)
                        .accessibilityHidden(true)
                }
                Text(item.name).foregroundStyle(Brian.text)
            }
            .font(BrianType.body)
            .frame(maxWidth: typeSize.isAccessibilitySize ? nil : .infinity, alignment: .leading)
            ProtocolStateLabel(row: item)
        }
        .accessibilityElement(children: .combine)
    }

    /// Today's Start → Stop spans (D-006): one collapsed row; expanded, one row per session,
    /// newest first. A row pushes the session's detail.
    private var sessionsCard: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let summary = appState.sessionsSummary(now: context.date)
            VStack(alignment: .leading, spacing: 16) {
                if summary.isEmpty {
                    Text(summary.title)
                        .font(BrianType.title)
                        .foregroundStyle(Brian.ink)
                        .accessibilityAddTraits(.isHeader)
                    Text(SessionsSummary.emptySentence)
                        .font(BrianType.body)
                        .foregroundStyle(Brian.muted)
                } else {
                    DisclosureGroup(isExpanded: $sessionsExpanded) {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(summary.rows) { session in
                                Divider().overlay(Brian.line)
                                Button { openSession = session.id } label: { sessionRow(session) }
                                    .buttonStyle(.plain)
                                    .accessibilityHint("Opens the session")
                            }
                        }
                        .padding(.top, 8)
                    } label: {
                        Text(summary.title)
                            .font(BrianType.title)
                            .foregroundStyle(Brian.ink)
                            .multilineTextAlignment(.leading)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    }
                    .tint(Brian.muted)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .panel()
        }
    }

    private func sessionRow(_ session: SessionsSummary.Row) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                let times = row(spacing: 8, alignment: .firstTextBaseline)
                times {
                    Text(session.span)
                        .font(BrianType.body.monospacedDigit())
                        .foregroundStyle(Brian.text)
                        .frame(maxWidth: typeSize.isAccessibilitySize ? nil : .infinity, alignment: .leading)
                    Text(session.duration)
                        .font(BrianType.secondary.monospacedDigit())
                        .foregroundStyle(Brian.muted)
                }
                if let headline = session.headline {
                    Text(headline)
                        .font(BrianType.secondary)
                        .foregroundStyle(Brian.muted)
                        .multilineTextAlignment(.leading)
                }
            }
            Image(systemName: "chevron.right")
                .font(BrianType.caption.weight(.semibold))
                .foregroundStyle(Brian.muted)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 12)
        .frame(minHeight: Space.logRow)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    /// "Today's stats" (D-007): eight cells that keep their place when empty. Two columns;
    /// one at accessibility sizes, where a time range would break a digit per line.
    private var statsSheet: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let cells = appState.todayStats(now: context.date)
            VStack(alignment: .leading, spacing: 16) {
                Text("Today's stats")
                    .font(BrianType.title)
                    .foregroundStyle(Brian.ink)
                    .accessibilityAddTraits(.isHeader)
                if typeSize.isAccessibilitySize {
                    ForEach(cells) { statCell($0) }
                } else {
                    // A Grid, so both cells of a row share the taller one's height.
                    Grid(horizontalSpacing: 16, verticalSpacing: 16) {
                        ForEach(Array(stride(from: 0, to: cells.count, by: 2)), id: \.self) { i in
                            GridRow {
                                statCell(cells[i])
                                if i + 1 < cells.count { statCell(cells[i + 1]) } else { Color.clear }
                            }
                        }
                    }
                }
            }
        }
    }

    private func statCell(_ cell: TodayStats.Cell) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(cell.value ?? cell.emptyWord)
                .font(cell.isEmpty ? BrianType.body : BrianType.number)
                .foregroundStyle(cell.isEmpty ? Brian.muted : Brian.ink)
                .fixedSize(horizontal: false, vertical: true)
            if let detail = cell.detail {
                Text(detail)
                    .font(BrianType.secondary.monospacedDigit())
                    .foregroundStyle(Brian.text)
            }
            Spacer(minLength: 0)
            Text(cell.label)
                .font(BrianType.secondary)
                .foregroundStyle(Brian.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(16)
        .background(Brian.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(cell.label), \(cell.text)")
    }

    private func summarySentence(_ text: String) -> some View {
        Text(text)
            .font(BrianType.body)
            .foregroundStyle(Brian.muted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var ledger: some View {
        VStack(alignment: .leading, spacing: 0) {
            // D-008 turns this into the collapsed "Log"; "Today" is the summary's title now.
            Text("Log")
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
        Ledger.entries(decisions: appState.decisions, episodes: appState.episodes)
    }

    private var heldBackCount: Int { Ledger.heldBackCount(appState.decisions) }
}
