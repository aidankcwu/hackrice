// DEMO_UI_PRD.md "Header": the bar above every tab. Status pill (battery, eyeglasses
// symbol, ConnectionStatus line), Start watching / Stop, Preview, Settings. Pure, like
// ConnectionStatus.derive, so every header state is testable without glasses.
import Foundation

struct HeaderState: Equatable {
    /// "82%", or nil when the glasses have not reported a battery reading.
    let battery: String?
    /// The eyeglasses symbol is ink when the glasses are worn or at least connected,
    /// muted when off or taken off.
    let glassesActive: Bool
    /// VoiceOver for the battery and the symbol: "Glasses worn, battery 82 percent".
    let glassesLabel: String
    let status: ConnectionStatus
    /// "Start watching" or "Stop".
    let primaryTitle: String
    /// Start waits for ConnectRows.canStart; Stop is never disabled.
    let primaryEnabled: Bool
    /// Preview needs glasses that are connected.
    let previewEnabled: Bool

    struct Inputs {
        var device: GlassesDeviceState?
        var glasses: GlassesState
        var status: ConnectionStatus
        var watching: Bool
        var canStart: Bool
    }

    static func derive(_ input: Inputs) -> HeaderState {
        let connected = input.glasses == .connected
        let worn = input.device?.worn
        let active = worn ?? connected
        let battery = input.device?.batteryLevel.map { min(100, max(0, $0)) }

        var label: String
        switch worn {
        case true?: label = "Glasses worn"
        case false?: label = "Glasses not worn"
        case nil: label = connected ? "Glasses connected" : "Glasses off"
        }
        if let battery {
            label += ", battery \(battery) percent"
            if input.device?.charging == true { label += ", charging" }
        }

        return HeaderState(
            battery: battery.map { "\($0)%" },
            glassesActive: active,
            glassesLabel: label,
            status: input.status,
            primaryTitle: input.watching ? "Stop" : "Start watching",
            primaryEnabled: input.watching || input.canStart,
            previewEnabled: connected)
    }
}
