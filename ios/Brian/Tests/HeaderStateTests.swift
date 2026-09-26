// DEMO_UI_PRD.md "Header": battery, eyeglasses symbol, status, Start / Stop, Preview.
import Testing
@testable import Brian

struct HeaderStateTests {
    private let watchingStatus = ConnectionStatus(level: .green, text: "Watching · 14 min")
    private let idleStatus = ConnectionStatus(level: .grey, text: "Not watching")
    private let offStatus = ConnectionStatus(level: .red, text: "Glasses off")

    private func derive(device: GlassesDeviceState?, glasses: GlassesState, status: ConnectionStatus,
                        watching: Bool = false, canStart: Bool = true) -> HeaderState {
        HeaderState.derive(.init(device: device, glasses: glasses, status: status,
                                 watching: watching, canStart: canStart))
    }

    @Test func unknownBatteryShowsNoPercent() {
        let header = derive(device: GlassesDeviceState(batteryLevel: nil, worn: nil),
                            glasses: .connected, status: idleStatus)
        #expect(header.battery == nil)
        #expect(header.glassesActive)
        #expect(header.glassesLabel == "Glasses connected")
        #expect(header.previewEnabled)
    }

    @Test func glassesOffIsMutedWithNoBatteryAndNoPreview() {
        let header = derive(device: nil, glasses: .unavailable, status: offStatus, canStart: false)
        #expect(header.battery == nil)
        #expect(!header.glassesActive)
        #expect(header.glassesLabel == "Glasses off")
        #expect(header.status == offStatus)
        #expect(header.primaryTitle == "Start watching")
        #expect(!header.primaryEnabled)
        #expect(!header.previewEnabled)
    }

    @Test func wornShowsBatteryAndInkSymbol() {
        let header = derive(device: .demo, glasses: .connected, status: idleStatus)
        #expect(header.battery == "82%")
        #expect(header.glassesActive)
        #expect(header.glassesLabel == "Glasses worn, battery 82 percent")
        #expect(header.primaryTitle == "Start watching")
        #expect(header.primaryEnabled)
    }

    @Test func takenOffIsMutedEvenWhileConnected() {
        let header = derive(device: GlassesDeviceState(batteryLevel: 40, charging: true, worn: false),
                            glasses: .connected, status: idleStatus)
        #expect(!header.glassesActive)
        #expect(header.glassesLabel == "Glasses not worn, battery 40 percent, charging")
    }

    @Test func watchingShowsStopAndStopIsNeverDisabled() {
        let header = derive(device: .demo, glasses: .connected, status: watchingStatus,
                            watching: true, canStart: false)
        #expect(header.primaryTitle == "Stop")
        #expect(header.primaryEnabled)
        #expect(header.status.text == "Watching · 14 min")
    }

    @Test func startingHasProgressTitleAndIsDisabled() {
        let header = HeaderState.derive(.init(device: .demo, glasses: .registered,
                                              status: idleStatus, watching: false,
                                              canStart: true, starting: true))
        #expect(header.primaryTitle == "Starting…")
        #expect(!header.primaryEnabled)
    }

    @Test func registeredButNotStreamingCannotPreview() {
        let header = derive(device: nil, glasses: .registered, status: idleStatus)
        #expect(!header.previewEnabled)
        #expect(!header.glassesActive)
    }

    @Test func batteryIsClamped() {
        let header = derive(device: GlassesDeviceState(batteryLevel: 130, worn: true),
                            glasses: .connected, status: idleStatus)
        #expect(header.battery == "100%")
    }
}
