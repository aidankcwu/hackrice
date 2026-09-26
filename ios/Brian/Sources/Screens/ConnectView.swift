// APP_PRD.md "Connect screen": the feedback fix. Three rows (Invite link, Glasses, Stream),
// each a dot, one line of live state and at most one button; red rows add the fix in one
// sentence. Then the one primary button, a live panel while watching, and the permissions
// collapsed at the bottom. Full screen on first launch; a sheet from the pill and Settings.
import SwiftUI

struct ConnectView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    /// Change / a refused link shows the paste field again. The applied link's token is
    /// never put back into it (AppState clears the draft once a link parses).
    @State private var isEditingLink = false
    @State private var askConsent = false

    var body: some View {
        NavigationStack {
            // Minutes, frames/s and "last frame N s ago" are time: re-read once a second.
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let rows = appState.connectRows(now: context.date)
                let stats = appState.streamStats(now: context.date)
                List {
                    Section {
                        inviteRow(rows.invite)
                        ConnectRowView(title: "Glasses", row: rows.glasses) { glassesButton }
                        ConnectRowView(title: "Stream", row: rows.stream) { EmptyView() }
                    }

                    Section {
                        primaryButton(canStart: rows.canStart)
                        if let error = appState.lastError, !rows.fixes.contains(error) {
                            Text(error)
                                .font(BrianType.secondary)
                                .foregroundStyle(Brian.cost)
                        }
                    }
                    .listRowBackground(Color.clear)

                    if appState.watching { livePanel(stats) }

                    Section {
                        DisclosureGroup("Permissions") {
                            ForEach(PermissionRow.Kind.allCases, id: \.self) { PermissionRow(kind: $0) }
                        }
                    }
                }
                .listStyle(.insetGrouped)
            }
            .navigationTitle("Connect")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        // Presentations do not inherit RootView's tint; ink, never system blue.
        .tint(Brian.ink)
        .task {
            if appState.linkChangeRequested {
                isEditingLink = true
                appState.linkChangeRequested = false
            }
            await appState.checkClipboard()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await appState.checkClipboard() } }
        }
        .streamingConsentSheet(isPresented: $askConsent) {
            appState.consentGiven = true
            Task { await appState.startWatching() }
        }
    }

    // MARK: Invite link

    @ViewBuilder
    private func inviteRow(_ row: ConnectRow) -> some View {
        ConnectRowView(title: "Invite link", row: row) {
            switch appState.link {
            case .reachable where !isEditingLink:
                Button("Change") { isEditingLink = true }.buttonStyle(.glass)
            case .unreachable where !isEditingLink:
                HStack {
                    Button("Try again") { Task { await appState.testServer() } }.buttonStyle(.glass)
                    Button("Change") { isEditingLink = true }.buttonStyle(.glass)
                }
            default:
                EmptyView()
            }
        }
        if showsPasteField {
            HStack(spacing: 16) {
                TextField("wss:// link from your invite", text: Bindable(appState).serverURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .submitLabel(.go)
                    .onSubmit(applyDraft)
                if !appState.serverURL.isEmpty {
                    Button("Use link", action: applyDraft).buttonStyle(.glass)
                }
            }
            .frame(minHeight: 44)
            if appState.clipboardOffer {
                Button {
                    Task {
                        await appState.useClipboardLink()
                        finishEditingIfApplied()
                    }
                } label: {
                    Label("Use the link on your clipboard", systemImage: "doc.on.clipboard")
                }
                .buttonStyle(.glass)
                .frame(minHeight: 44)
            }
        }
    }

    private var showsPasteField: Bool {
        if isEditingLink { return true }
        if case .notSet = appState.link { return true }
        return false
    }

    private func applyDraft() {
        Task {
            await appState.applyServerURL(appState.serverURL)
            finishEditingIfApplied()
        }
    }

    private func finishEditingIfApplied() {
        if case .reachable = appState.link { isEditingLink = false }
    }

    // MARK: Glasses

    @ViewBuilder
    private var glassesButton: some View {
        switch appState.glasses {
        case .unavailable:
            Button("Open Meta AI") { appState.openMetaAI() }.buttonStyle(.glass)
        case .notRegistered where !appState.registeringGlasses:
            Button("Register") { Task { await appState.registerGlasses() } }.buttonStyle(.glass)
        default:
            EmptyView()
        }
    }

    // MARK: Start / Stop

    /// Disabled (dimmed) until the rows above are ready; Stop is always live.
    private func primaryButton(canStart: Bool) -> some View {
        Button {
            if appState.watching {
                Task { await appState.stopWatching() }
            } else if appState.consentGiven || StreamingConsent.isGranted {
                appState.consentGiven = true
                Task { await appState.startWatching() }
            } else {
                askConsent = true
            }
        } label: {
            // The ink tint is near-white in dark mode: the page colour keeps the label readable.
            Text(appState.watching ? "Stop" : (appState.startingWatch ? "Starting…" : "Start watching"))
                .foregroundStyle(Brian.page)
        }
        .buttonStyle(.glassProminent)
        .controlSize(.large)
        .frame(maxWidth: .infinity)
        .disabled(appState.startingWatch || (!appState.watching && !canStart))
    }

    // MARK: Live panel

    private func livePanel(_ stats: StreamStats) -> some View {
        Section("Live") {
            LabeledContent("Frames sent") {
                Text(stats.framesSent, format: .number).monospacedDigit()
            }
            LabeledContent("Server acknowledged", value: stats.serverAcknowledged ? "Yes" : "No")
            Text(spokenLine(stats))
                .font(BrianType.secondary)
                .foregroundStyle(Brian.text)
            Button("Test voice") { Task { await appState.sayTestLine() } }
                .buttonStyle(.glass)
                .frame(minHeight: 44)
        }
    }

    private func spokenLine(_ stats: StreamStats) -> String {
        guard let text = stats.lastSpokenText, let at = stats.lastSpokenAt else {
            return "Bryan has not spoken yet"
        }
        return "Bryan last said, \(at.formatted(date: .omitted, time: .shortened)): “\(text)”"
    }
}

/// One Connect row: title, then a dot beside one line of state, then the fix when red.
private struct ConnectRowView<Action: View>: View {
    let title: String
    let row: ConnectRow
    @ViewBuilder var action: Action
    /// Puts the dot on the middle of the state line's first line at any text size.
    @ScaledMetric(relativeTo: .subheadline) private var dotInset: CGFloat = 6
    @ScaledMetric(relativeTo: .subheadline) private var dotSize: CGFloat = 8
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // At accessibility sizes the button goes under the text, or "Change" breaks mid-word.
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 16))
        layout {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(BrianType.body)
                    .foregroundStyle(Brian.ink)
                HStack(alignment: .top, spacing: 8) {
                    Circle()
                        .fill(row.level.color)
                        .frame(width: dotSize, height: dotSize)
                        .padding(.top, dotInset)
                        .accessibilityHidden(true)
                    // At accessibility sizes each " · " part takes its own line, and a part
                    // with no space (an address like 10.0.0.5:8010) shrinks rather than
                    // breaking a digit onto the next line.
                    if typeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(row.text.components(separatedBy: " · ").enumerated()), id: \.offset) { _, part in
                                Text(part)
                                    .lineLimit(part.contains(" ") ? nil : 1)
                                    .minimumScaleFactor(part.contains(" ") ? 1 : 0.5)
                            }
                        }
                        .font(BrianType.secondary.monospacedDigit())
                        .foregroundStyle(Brian.muted)
                    } else {
                        Text(row.text)
                            .font(BrianType.secondary.monospacedDigit())
                            .foregroundStyle(Brian.muted)
                    }
                }
                if let fix = row.fix {
                    Text(fix)
                        .font(BrianType.secondary)
                        .foregroundStyle(Brian.cost)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
            action
        }
        .padding(.vertical, 4)
        .frame(minHeight: 44)
    }
}

private extension ConnectRows {
    /// Sentences already shown on a row, so the error line under the button never repeats one.
    var fixes: [String] { [invite.fix, glasses.fix, stream.fix].compactMap { $0 } }
}
