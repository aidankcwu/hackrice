// Picks the GlassesSessioning implementation once, at launch.
//
//   demo (-demo / BRIAN_DEMO=1)         MockGlasses in DEBUG, else DemoGlasses (below)
//   Settings "Use mock glasses" (DEBUG)  MockGlasses (read at launch; relaunch to switch)
//   otherwise                            GlassesSession (Meta DAT)
import Foundation
import UIKit

@MainActor
enum GlassesFactory {
    /// Settings' "Use mock glasses" toggle (`@AppStorage("useMockGlasses")`).
    static let useMockKey = "useMockGlasses"
    private static weak var activeSession: (any GlassesSessioning)?

    static func make(demo: Bool, defaults: UserDefaults = .standard) -> GlassesSessioning {
        let session: GlassesSessioning
#if DEBUG
        if demo || defaults.bool(forKey: useMockKey) {
            session = MockGlasses()
        } else {
            session = GlassesSession()
        }
#else
        if demo {
            session = DemoGlasses()
        } else {
            session = GlassesSession()
        }
#endif
        activeSession = session
        return session
    }

    static func handleIncomingURL(_ url: URL) async {
        await activeSession?.handleIncomingURL(url)
    }
}

/// Reports Connected and does nothing else. Demo mode in a Release build, where
/// MockGlasses (DEBUG only) does not exist.
@MainActor
final class DemoGlasses: GlassesSessioning {
    private(set) var state: GlassesState = .connected
    var onStateChange: ((GlassesState) -> Void)?
    private(set) var deviceState: GlassesDeviceState? = .demo
    var onDeviceStateChange: ((GlassesDeviceState?) -> Void)?
    var onPreviewFrame: ((UIImage, Date) -> Void)?
    func openMetaAI() {}
    func register() async throws {}
    func handleIncomingURL(_ url: URL) async {}
    func startStream() async throws {}
    func stopStream() {}
}
