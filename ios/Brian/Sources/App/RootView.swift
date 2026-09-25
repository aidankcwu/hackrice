// DEMO_UI_PRD.md "The product in one screen": three tabs (Home, Analysis, Protocol; the
// middle one is the web app) under one fixed header (AppHeader: status pill, Start
// watching / Stop, Preview, Settings). Connect opens full screen on first launch.
// RootView owns the navigation stacks and every sheet the header opens.
import SwiftUI

/// Screens a `-screen <id>` launch argument can open on (screenshots, APP_NATIVE_NOTES.md).
enum AppScreen: String, CaseIterable {
    case home, analysis, `protocol`, connect, settings, preview
    /// Home with the hero's "How this is measured" sheet up (screenshots).
    case measured
    /// The Protocol tab scrolled to Templates with Doses open, and with the first item's
    /// edit sheet up (screenshots).
    case protocolTemplates = "protocol-templates"
    case protocolEdit = "protocol-edit"
    /// Home with the newest session's detail pushed (screenshots).
    case session

    static let argument = "-screen"

    /// The value after `-screen`, or nil when the argument is absent or unknown.
    /// `today` is the old name of `home`, kept for existing scripts.
    static func from(arguments: [String]) -> AppScreen? {
        guard let index = arguments.firstIndex(of: argument), index + 1 < arguments.count else { return nil }
        let value = arguments[index + 1].lowercased()
        return value == "today" ? .home : AppScreen(rawValue: value)
    }

    /// The tab this screen lives on. Connect, Settings, Preview and Measured are sheets over Home.
    var tab: AppTab {
        switch self {
        case .analysis: .analysis
        case .protocol, .protocolTemplates, .protocolEdit: .protocol
        case .home, .connect, .settings, .preview, .measured, .session: .home
        }
    }
}

/// Exactly three tabs, in this order (DEMO_UI_PRD.md).
enum AppTab: Hashable, CaseIterable {
    case home, analysis, `protocol`
}

struct RootView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: AppTab = .home
    /// Connect from the pill or Settings: a sheet over the tabs.
    @State private var showConnect = false
    /// Connect on first launch: full screen, closed with Done.
    @State private var showConnectFullScreen = false
    @State private var showSettings = false
    @State private var showPreview = false
    @State private var showMeasured = false
    /// The consent gate before the first stream, asked by the header's Start watching.
    @State private var askConsent = false
    @State private var bootstrapped = false

    /// Set once Connect has been closed, so it opens by itself only once. The key keeps
    /// its Setup-era name so existing installs do not see it again.
    static let connectSeenKey = "setupSeen"
    /// Older spelling of `-screen connect`, kept for existing screenshot scripts.
    static let showSetupArgument = "-showSetup"

    var body: some View {
        TabView(selection: $tab) {
            Tab("Home", systemImage: "house", value: AppTab.home) {
                NavigationStack {
                    HomeView(openMeasured: { showMeasured = true }, openProtocol: { tab = .protocol }).modifier(header)
                }
            }
            Tab("Analysis", systemImage: "chart.bar", value: AppTab.analysis) {
                NavigationStack {
                    WebScreen(title: "Analysis", path: "/analysis").modifier(header)
                }
            }
            Tab("Protocol", systemImage: "pills", value: AppTab.protocol) {
                NavigationStack {
                    ProtocolView().modifier(header)
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
        .sheet(isPresented: $showPreview) {
            PreviewView()
        }
        .sheet(isPresented: $showMeasured) {
            MeasuredView()
        }
        .streamingConsentSheet(isPresented: $askConsent) {
            appState.consentGiven = true
            Task { await appState.startWatching() }
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
            if appState.demo, screen == .protocolTemplates || screen == .protocolEdit {
                appState.protocolLaunch = screen
            }
            if appState.demo, screen == .session { appState.homeLaunch = screen }
            let forced = arguments.contains(Self.showSetupArgument) || screen == .connect
            let firstLaunch = !appState.demo && !UserDefaults.standard.bool(forKey: Self.connectSeenKey)
            if forced || (firstLaunch && screen == nil) {
                showConnectFullScreen = true
            } else if screen == .settings {
                showSettings = true
            } else if screen == .preview {
                showPreview = true
            } else if screen == .measured {
                showMeasured = true
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

    private var header: AppHeader {
        AppHeader(
            askConsent: { askConsent = true },
            openPreview: { showPreview = true },
            openSettings: { showSettings = true })
    }
}
