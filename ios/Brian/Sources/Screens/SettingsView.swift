// APP_PRD.md "Settings": invite link (label + Change), Connect, voice, permissions, record
// corpus, version. The link is only ever pasted in Connect; "Change" opens it there.
import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .subheadline) private var dotSize: CGFloat = 8
    @AppStorage("speakThroughGlasses") private var speakThroughGlasses = true
#if DEBUG
    @AppStorage("useMockGlasses") private var useMockGlasses = false
#endif

    var body: some View {
        NavigationStack {
            List {
                Section("Invite link") {
                    // At accessibility sizes the button goes under the link, or "Change"
                    // breaks mid-word and the address splits at the colon.
                    let layout = typeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                        : AnyLayout(HStackLayout(spacing: 16))
                    layout {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(linkLabel)
                                .font(BrianType.body)
                                .foregroundStyle(appState.link == .notSet ? Brian.muted : Brian.text)
                            if case .unreachable(let sentence) = appState.link {
                                Text(sentence)
                                    .font(BrianType.secondary)
                                    .foregroundStyle(Brian.cost)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Button(appState.link == .notSet ? "Paste link" : "Change") {
                            appState.requestLinkChange()
                        }
                        .buttonStyle(.glass)
                    }
                    .frame(minHeight: 44)
                }

                Section {
                    Button {
                        appState.requestConnect()
                    } label: {
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            let status = appState.connectionStatus(now: context.date)
                            LabeledContent {
                                HStack(alignment: .firstTextBaseline, spacing: 8) {
                                    Circle()
                                        .fill(status.level.color)
                                        .frame(width: dotSize, height: dotSize)
                                        .accessibilityHidden(true)
                                    Text(status.text)
                                        .font(BrianType.secondary.monospacedDigit())
                                        .foregroundStyle(Brian.muted)
                                }
                            } label: {
                                Text("Connect").foregroundStyle(Brian.ink)
                            }
                        }
                    }
                    .frame(minHeight: 44)
                }

                Section {
                    Toggle("Speak through the glasses", isOn: $speakThroughGlasses)
                        .frame(minHeight: 44)
                } header: {
                    Text("Voice")
                } footer: {
                    Text("Off, or with the glasses away, Bryan's lines arrive as notifications instead.")
                }

                Section {
                    DisclosureGroup("Permissions") {
                        ForEach(PermissionRow.Kind.allCases, id: \.self) { PermissionRow(kind: $0) }
                    }
                    .frame(minHeight: 44)
                }

                Section {
                    Toggle("Record corpus", isOn: Bindable(appState).recordCorpusEnabled)
                        .frame(minHeight: 44)
                } footer: {
                    Text("Saves one frame a second to this phone while watching, for tuning. Leave off unless asked.")
                }

#if DEBUG
                Section("Debug") {
                    Text(appState.linkStatusLine).foregroundStyle(Brian.muted)
                    Toggle("Use mock glasses", isOn: $useMockGlasses)
                }
#endif

                Section("About") {
                    LabeledContent("Version", value: info("CFBundleShortVersionString"))
                    LabeledContent("Build", value: info("CFBundleVersion"))
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        // Presentations do not inherit RootView's tint; ink, never system blue.
        .tint(Brian.ink)
    }

    /// Token-free: the reachable label, else the applied endpoint, else "Not set".
    private var linkLabel: String {
        switch appState.link {
        case .reachable(let label): label
        case .unreachable: appState.endpointLabel ?? "Not set"
        case .notSet: "Not set"
        }
    }

    private func info(_ key: String) -> String {
        Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "—"
    }
}
