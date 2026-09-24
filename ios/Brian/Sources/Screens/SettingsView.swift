import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @AppStorage("speakThroughGlasses") private var speakThroughGlasses = true
    // Redacted-label state is view-local: once a link parses, AppState clears the
    // paste box (never round-trips the token into it) and this flag decides which of
    // the two rows below is shown. Demo mode is unchanged — it always shows the field.
    @State private var isEditingServer = false
#if DEBUG
    @AppStorage("useMockGlasses") private var useMockGlasses = false
#endif

    var body: some View {
        NavigationStack {
            List {
                Section("Server") {
                    if let endpointLabel = appState.endpointLabel, !appState.demo, !isEditingServer {
                        HStack {
                            Text(endpointLabel)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Button("Change") { isEditingServer = true }
                        }
                    } else {
                        TextField("Server URL", text: Bindable(appState).serverURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        Button("Test") {
                            Task {
                                await appState.applyServerURL(appState.serverURL)
                                await appState.testServer()
                                if appState.endpointLabel != nil { isEditingServer = false }
                            }
                        }
                    }
                }

                Section("Voice") {
                    Toggle("Speak through the glasses", isOn: $speakThroughGlasses)
                }

                Section {
                    Button("Connect") {
                        appState.requestConnect()
                        dismiss()
                    }
                }

#if DEBUG
                Section("Debug") {
                    Text(debugStatusLine).foregroundStyle(.secondary)
                    Toggle("Record corpus", isOn: Bindable(appState).recordCorpusEnabled)
                    Button("Say a test line") { Task { await appState.sayTestLine() } }
                    Toggle("Use mock glasses", isOn: $useMockGlasses)
                }
#endif

                Section("About") {
                    LabeledContent("Version", value: version)
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
    }

#if DEBUG
    private var debugStatusLine: String {
        appState.linkStatusLine
    }
#endif

    private var version: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        return build.map { "\(version) (\($0))" } ?? version
    }
}
