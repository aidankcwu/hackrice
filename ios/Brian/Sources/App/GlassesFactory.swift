// Picks the GlassesSessioning implementation once, at launch.
//
//   demo (-demo / BRIAN_DEMO=1)         MockGlasses in DEBUG, else DemoGlasses (below)
//   Settings "Use mock glasses" (DEBUG)  MockGlasses (read at launch; relaunch to switch)
//   otherwise                            GlassesSession (Meta DAT), or UnconfiguredGlasses
//                                        when DAT will not configure (the build lacks its Meta setup)
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
            session = GlassesSession.live()
        }
#else
        if demo {
            session = DemoGlasses()
        } else {
            session = GlassesSession.live()
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
    var onRegistrationFailure: ((String) -> Void)?
    func openMetaAI() {}
    func register() async throws {}
    func handleIncomingURL(_ url: URL) async {}
    func startStream() async throws {}
    func stopStream() {}
}

/// Stands in when `Wearables.configure()` failed at launch: DAT must not be touched after
/// that, so Register and Start watching say the build lacks its Meta setup instead.
@MainActor
final class UnconfiguredGlasses: GlassesSessioning {
    private(set) var state: GlassesState = .notRegistered
    var onStateChange: ((GlassesState) -> Void)?
    private(set) var deviceState: GlassesDeviceState? = nil
    var onDeviceStateChange: ((GlassesDeviceState?) -> Void)?
    var onPreviewFrame: ((UIImage, Date) -> Void)?
    var onRegistrationFailure: ((String) -> Void)?
    func openMetaAI() {
        guard let url = URL(string: "fb-viewapp://") else { return }
        UIApplication.shared.open(url)
    }
    func register() async throws { throw GlassesSessionError.notConfigured }
    func handleIncomingURL(_ url: URL) async {}
    func startStream() async throws { throw GlassesSessionError.notConfigured }
    func stopStream() {}
}
