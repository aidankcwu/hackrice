// DEMO_UI_PRD.md "Home · 4. Sessions": one Start → Stop span. Its length, the recap the
// backend wrote when it ended (or "Summary still writing…" and Refresh), then the log rows
// that fall inside it.
import SwiftUI

struct SessionDetailView: View {
    @Environment(AppState.self) private var appState
    let sessionID: String

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            ScrollView {
                if let session = appState.sessionsSummary(now: context.date).rows.first(where: { $0.id == sessionID }) {
                    content(session, now: context.date)
                } else {
                    // The session fell out of today's list (past midnight): say so, one button back.
                    Text("This session is not part of today any more.")
                        .font(BrianType.body)
                        .foregroundStyle(Brian.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Space.gutter)
                        .padding(.vertical, 24)
                }
            }
        }
        .background(Brian.page)
        .navigationTitle("Session")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await appState.refreshSessions() }
    }

    private func content(_ session: SessionsSummary.Row, now: Date) -> some View {
        VStack(alignment: .leading, spacing: Space.section) {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.duration)
                    .font(BrianType.number)
                    .foregroundStyle(Brian.ink)
                Text(session.span)
                    .font(BrianType.secondary.monospacedDigit())
                    .foregroundStyle(Brian.muted)
            }
            .accessibilityElement(children: .combine)

            recap(session)
            log(session, now: now)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Space.gutter)
        .padding(.vertical, 24)
    }

    private func recap(_ session: SessionsSummary.Row) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Summary")
                .font(BrianType.title)
                .foregroundStyle(Brian.ink)
                .accessibilityAddTraits(.isHeader)
            switch session.recapState {
            case .recap(let recap):
                RecapText(recap: recap)
            case .running:
                sentence(SessionsSummary.runningSentence)
            case .writing:
                sentence(SessionsSummary.writingSentence)
                Button("Refresh") { Task { await appState.refreshSessions() } }
                    .buttonStyle(.glass)
                    .disabled(appState.sessionsLoading)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .panel()
    }

    private func log(_ session: SessionsSummary.Row, now: Date) -> some View {
        let all = Ledger.entries(decisions: appState.decisions, episodes: appState.episodes)
        let entries = SessionsSummary.entries(all, in: session.session, now: now)
        return VStack(alignment: .leading, spacing: 0) {
            Text("Log")
                .font(BrianType.title)
                .foregroundStyle(Brian.ink)
                .accessibilityAddTraits(.isHeader)
                .padding(.bottom, 8)
            if entries.isEmpty {
                sentence(SessionsSummary.nothingLoggedSentence)
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
        }
    }

    private func sentence(_ text: String) -> some View {
        Text(text)
            .font(BrianType.body)
            .foregroundStyle(Brian.muted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
