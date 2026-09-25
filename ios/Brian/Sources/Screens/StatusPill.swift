// DEMO_UI_PRD.md "Header", above every tab and fixed while the page scrolls. The
// navigation bar carries the status pill (leading), Preview and the Settings gear
// (trailing); Start watching / Stop is a compact capsule sized to its label, centred in a
// system safe-area bar right under it, so it stays put while the page scrolls. All four in
// one bar do not fit a 402 pt phone: even beside the short "Glasses off" pill, "Start
// watching" pushes Preview and the gear into a "•••" menu. RootView owns the sheets.
import SwiftUI

struct StatusPill: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        // "Watching · 12 min" is time, so the pill re-reads the status once a second.
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let header = appState.headerState(now: context.date)
            Button {
                appState.requestConnect()
            } label: {
                HStack(spacing: 6) {
                    if let battery = header.battery {
                        Text(battery)
                            .font(BrianType.secondary.monospacedDigit())
                            .foregroundStyle(Brian.text)
                    }
                    Image(systemName: BrianSymbol.glasses)
                        .font(BrianType.secondary)
                        .foregroundStyle(header.glassesActive ? Brian.ink : Brian.muted)
                    Circle()
                        .fill(header.status.level.color)
                        .frame(width: 8, height: 8)
                    Text(header.status.text)
                        .font(BrianType.secondary.monospacedDigit())
                        .foregroundStyle(Brian.text)
                }
                .lineLimit(1)
                .fixedSize()
                // Bar text stops growing at the default size, as the system's own bar
                // items do, or Preview and the gear fold into a "•••" menu; a long press
                // shows it large.
                .dynamicTypeSize(...DynamicTypeSize.large)
                .padding(.horizontal, 4)
                .frame(minHeight: 44)
                .contentShape(Capsule())
            }
            .accessibilityShowsLargeContentViewer {
                Label("\(header.battery.map { $0 + " · " } ?? "")\(header.status.text)", systemImage: BrianSymbol.glasses)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(header.glassesLabel). \(header.status.text)")
            .accessibilityHint("Opens Connect")
        }
    }
}

/// Start watching / Stop: the one `.glassProminent` control on screen. Same behaviour and
/// consent gate as Connect's button; disabled until ConnectRows.canStart.
struct WatchButton: View {
    @Environment(AppState.self) private var appState
    let askConsent: () -> Void

    var body: some View {
        let header = appState.headerState(now: .now)
        Button {
            if appState.watching {
                Task { await appState.stopWatching() }
            } else if appState.consentGiven || StreamingConsent.isGranted {
                appState.consentGiven = true
                Task { await appState.startWatching() }
            } else {
                askConsent()
            }
        } label: {
            // The tint is Brian.ink, near-white in dark mode: the label takes the page colour
            // so it stays readable on the fill in both appearances.
            Text(header.primaryTitle)
                .foregroundStyle(Brian.page)
                .padding(.horizontal, 8)
        }
        .buttonStyle(.glassProminent)
        .controlSize(.large)
        // Header text stops growing where the system's bar buttons do; a long press shows it large.
        .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
        .accessibilityShowsLargeContentViewer()
        .disabled(!header.primaryEnabled)
    }
}

/// Opens the glasses' live view. Disabled unless the glasses are connected.
struct PreviewButton: View {
    @Environment(AppState.self) private var appState
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            Image(systemName: BrianSymbol.preview)
        }
        .disabled(!appState.headerState(now: .now).previewEnabled)
        .dynamicTypeSize(...DynamicTypeSize.large)
        .accessibilityShowsLargeContentViewer()
        .accessibilityLabel("Glasses view")
    }
}

/// The same header on every tab: the toolbar, plus the Start / Stop bar pinned under it.
struct AppHeader: ViewModifier {
    let askConsent: () -> Void
    let openPreview: () -> Void
    let openSettings: () -> Void

    func body(content: Content) -> some View {
        content
            // A large title would sit under the Start / Stop bar's scroll-edge fade.
            .toolbarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    StatusPill()
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    PreviewButton(open: openPreview)
                    Button(action: openSettings) {
                        Image(systemName: BrianSymbol.settings)
                    }
                    .dynamicTypeSize(...DynamicTypeSize.large)
                    .accessibilityShowsLargeContentViewer()
                    .accessibilityLabel("Settings")
                }
            }
            .safeAreaBar(edge: .top) {
                WatchButton(askConsent: askConsent)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, Space.gutter)
                    .padding(.vertical, 8)
            }
            // The capsule leaves the bar's sides open: a hard edge keeps rows from
            // scrolling legibly beside it.
            .scrollEdgeEffectStyle(.hard, for: .top)
    }
}
