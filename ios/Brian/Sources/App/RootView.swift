// IOS_SPEC.md "Structure": Setup as a sheet on first launch, a two-tab TabView (Today,
// Protocol), and a Settings gear in both tabs' toolbars. RootView owns the navigation
// stacks for Today and Protocol; SetupView and SettingsView bring their own.
import SwiftUI

struct RootView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSetup = false
    @State private var showSettings = false
    @State private var bootstrapped = false

    /// Set once Setup has been closed (Done or Later), so it opens by itself only once.
    static let setupSeenKey = "setupSeen"
    /// Launch argument that opens Setup on launch even in demo mode (screenshots).
    static let showSetupArgument = "-showSetup"

    var body: some View {
        TabView {
            Tab("Today", systemImage: "list.bullet") {
                NavigationStack {
                    TodayView()
                        .toolbar { settingsButton }
                }
            }
            Tab("Protocol", systemImage: "pills") {
                NavigationStack {
                    ProtocolView()
                        .toolbar { settingsButton }
                }
            }
        }
        .tint(Brian.ink)
        .sheet(isPresented: $showSetup, onDismiss: {
            UserDefaults.standard.set(true, forKey: Self.setupSeenKey)
        }) {
            SetupView()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .onChange(of: appState.setupRequested) { _, requested in
            guard requested else { return }
            showSettings = false
            showSetup = true
            appState.setupRequested = false
        }
        .task {
            guard !bootstrapped else { return }
            bootstrapped = true
            let forced = ProcessInfo.processInfo.arguments.contains(Self.showSetupArgument)
            let firstLaunch = !appState.demo && !UserDefaults.standard.bool(forKey: Self.setupSeenKey)
            if forced || firstLaunch { showSetup = true }
            await appState.bootstrap()
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: Task { await appState.didBecomeActive() }
            case .background: appState.didEnterBackground()
            default: break
            }
        }
        .onOpenURL { url in
            Task {
                await GlassesFactory.handleIncomingURL(url)
            }
        }
    }

    @ToolbarContentBuilder
    private var settingsButton: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Button {
                showSettings = true
            } label: {
                Image(systemName: BrianSymbol.settings)
            }
            .accessibilityLabel("Settings")
        }
    }
}
