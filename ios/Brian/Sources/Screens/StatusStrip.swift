import SwiftUI

struct StatusStrip: View {
    @Environment(AppState.self) private var appState
    let openSetup: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let problem {
                HStack(spacing: 12) {
                    Image(systemName: problem.symbol)
                        .frame(width: 20)
                        .accessibilityHidden(true)
                    Text(problem.text)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button(problem.button, action: problem.action)
                        .buttonStyle(.glass)
                }
                .font(BrianType.secondary)
                .foregroundStyle(Brian.cost)
                .frame(minHeight: 44)
            } else {
                statusRow("eyeglasses", glassesText)
                statusRow("desktopcomputer", backendText)
                statusRow("record.circle", watchingText)
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var problem: (symbol: String, text: String, button: String, action: () -> Void)? {
        if appState.glasses == .unavailable || appState.glasses == .notRegistered {
            return ("eyeglasses", "Glasses off", "Open Setup", openSetup)
        }
        switch appState.link {
        case .notSet:
            return ("desktopcomputer", "Backend not set", "Open Setup", openSetup)
        case .unreachable(let message):
            return ("desktopcomputer", message, "Retry", { Task { await appState.testServer() } })
        case .reachable:
            break
        }
        if let error = appState.lastError {
            return ("exclamationmark.circle", error, "Retry", { Task { await appState.refreshToday() } })
        }
        return nil
    }

    private var glassesText: String {
        appState.glasses == .connected ? "Glasses connected" : "Glasses off"
    }

    private var backendText: String {
        if case .reachable(let endpoint) = appState.link {
            if appState.watching && !appState.backendConnected {
                return "Backend unreachable"
            }
            return "Backend \(endpoint)"
        }
        return "Backend unreachable"
    }

    private var watchingText: String {
        guard appState.watching else { return "Not watching" }
        guard let since = appState.watchingSince else { return "Watching" }
        let minutes = max(0, Int(Date().timeIntervalSince(since) / 60))
        return "Watching \(minutes) min"
    }

    private func statusRow(_ symbol: String, _ text: String) -> some View {
        Label(text, systemImage: symbol)
            .font(BrianType.secondary)
            .foregroundStyle(Brian.text)
            .symbolRenderingMode(.monochrome)
            .frame(minHeight: Space.statusRow)
    }
}
