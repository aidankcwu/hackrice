// IOS_SPEC.md "Structure" + APP_PRD.md: Connect full screen on first launch, the four tabs
// (Today, Calendar, Analysis, Protocol; the middle two are the web app), and
// a Settings gear in every tab's toolbar. Every tab carries the status pill above its
// content; tapping it asks AppState for Connect, shown as a sheet.
// RootView owns the navigation stacks; ConnectView and SettingsView bring their own.
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

    /// The tab this screen lives on. Connect and Settings are sheets over Today.
    var tab: AppTab {
        switch self {
        case .calendar: .calendar
        case .analysis: .analysis
        case .protocol: .protocol
        case .today, .connect, .settings: .today
        }
    }
}

/// Exactly four tabs, in this order (APP_PRD.md "The product").
enum AppTab: Hashable, CaseIterable {
    case today, calendar, analysis, `protocol`
}

struct RootView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab = .today
    /// Connect from the pill or Settings: a sheet over the tabs.
    @State private var showConnect = false
    /// Connect on first launch: full screen, closed with Done.
    @State private var showConnectFullScreen = false
    @State private var showSettings = false
    @State private var bootstrapped = false

    /// Set once Connect has been closed, so it opens by itself only once. The key keeps
    /// its Setup-era name so existing installs do not see it again.
    static let connectSeenKey = "setupSeen"
    /// Older spelling of `-screen connect`, kept for existing screenshot scripts.
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
            Tab("Calendar", systemImage: "calendar", value: AppTab.calendar) {
                NavigationStack {
                    WebScreen(title: "Calendar", path: "/calendar")
                        .toolbar {
                            StatusPillToolbar()
                            settingsButton
                        }
                }
            }
            Tab("Analysis", systemImage: "chart.bar", value: AppTab.analysis) {
                NavigationStack {
                    WebScreen(title: "Analysis", path: "/analysis")
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
        .fullScreenCover(isPresented: $showConnectFullScreen, onDismiss: markConnectSeen) {
            ConnectView()
        }
        .sheet(isPresented: $showConnect, onDismiss: markConnectSeen) {
            ConnectView()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .onChange(of: appState.connectRequested) { _, requested in
            guard requested else { return }
            showSettings = false
            showConnect = true
            appState.connectRequested = false
        }
        .task {
            guard !bootstrapped else { return }
            bootstrapped = true
            let arguments = ProcessInfo.processInfo.arguments
            let screen = AppScreen.from(arguments: arguments)
            if let screen { tab = screen.tab }
            let forced = arguments.contains(Self.showSetupArgument) || screen == .connect
            let firstLaunch = !appState.demo && !UserDefaults.standard.bool(forKey: Self.connectSeenKey)
            if forced || (firstLaunch && screen == nil) {
                showConnectFullScreen = true
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

    private func markConnectSeen() {
        UserDefaults.standard.set(true, forKey: Self.connectSeenKey)
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
