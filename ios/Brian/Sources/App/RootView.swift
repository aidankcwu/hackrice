// IOS_SPEC.md "Structure" + APP_PRD.md: Setup as a sheet on first launch, the tabs, and a
// Settings gear in every tab's toolbar. Every tab carries the status pill above its
// content; tapping it asks AppState for Setup (Connect replaces Setup in N-003).
// RootView owns the navigation stacks; SetupView and SettingsView bring their own.
import SwiftUI

/// Screens a `-screen <id>` launch argument can open on (screenshots, APP_NATIVE_NOTES.md).
enum AppScreen: String, CaseIterable {
    case today, calendar, analysis, `protocol`, connect, settings

    static let argument = "-screen"

    /// The value after `-screen`, or nil when the argument is absent or unknown.
    static func from(arguments: [String]) -> AppScreen? {
        guard let index = arguments.firstIndex(of: argument), index + 1 < arguments.count else { return nil }
        return AppScreen(rawValue: arguments[index + 1].lowercased())
    }

    /// The tab this screen lives on. Calendar and Analysis land on Today until they exist
    /// (N-004); Connect and Settings are sheets over Today.
    var tab: AppTab {
        self == .protocol ? .protocol : .today
    }
}

enum AppTab: Hashable {
    case today, `protocol`
}

struct RootView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab = .today
    @State private var showSetup = false
    @State private var showSettings = false
    @State private var bootstrapped = false

    /// Set once Setup has been closed (Done or Later), so it opens by itself only once.
    static let setupSeenKey = "setupSeen"
    /// Launch argument that opens Setup on launch even in demo mode (screenshots).
    static let showSetupArgument = "-showSetup"

    var body: some View {
        TabView(selection: $tab) {
            Tab("Today", systemImage: "list.bullet", value: AppTab.today) {
                NavigationStack {
                    TodayView()
                        .toolbar {
                            StatusPillToolbar()
                            settingsButton
                        }
                }
            }
            Tab("Protocol", systemImage: "pills", value: AppTab.protocol) {
                NavigationStack {
                    ProtocolView()
                        .toolbar {
                            StatusPillToolbar()
                            settingsButton
                        }
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
            let arguments = ProcessInfo.processInfo.arguments
            let screen = AppScreen.from(arguments: arguments)
            if let screen { tab = screen.tab }
            let forced = arguments.contains(Self.showSetupArgument) || screen == .connect
            let firstLaunch = !appState.demo && !UserDefaults.standard.bool(forKey: Self.setupSeenKey)
            if forced || (firstLaunch && screen == nil) {
                showSetup = true
            } else if screen == .settings {
                showSettings = true
            }
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
