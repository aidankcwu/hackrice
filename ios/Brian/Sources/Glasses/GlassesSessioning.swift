// The one seam between the glasses and the rest of the app (IOS_SPEC.md "Glasses/").
// Coder B implements it twice: GlassesSession (Meta DAT, ported from the CameraAccess
// sample's WearablesViewModel + CameraViewModel) and MockGlasses (DEBUG, no hardware).
// Frames never cross this seam: the implementation hands them straight to
// CapturePacketSender, exactly as the sample app does today.
import Foundation
import UIKit

enum GlassesState: Equatable {
    case unavailable      // SDK reports no device / not paired in Meta AI
    case notRegistered    // paired but this app is not registered with the glasses
    case registered
    case connected        // a DAT session is up; frames flow while streaming
}

@MainActor
protocol GlassesSessioning: AnyObject {
    var state: GlassesState { get }
    var onStateChange: ((GlassesState) -> Void)? { get set }
    /// Deep-link to the Meta AI app for pairing / Developer Mode.
    func openMetaAI()
    /// DAT registration flow. Throws with a sentence a person can act on.
    func register() async throws
    /// Hands Meta AI's registration callback to DAT. Mock and demo sessions ignore it.
    func handleIncomingURL(_ url: URL) async
    /// Start the camera stream at the cadence CapturePacketSender expects; frames go to
    /// the sender internally. Idempotent.
    func startStream() async throws
    func stopStream()
}

/// Internal wiring seam: frame-producing sessions receive the sender owned by `Link`.
@MainActor
protocol CaptureSenderConfigurable: AnyObject {
    func useCaptureSender(_ sender: CapturePacketSender)
}

/// Optional diagnostic seam. Link owns the opt-in recorder and sessions offer frames.
@MainActor
protocol CorpusRecordingConfigurable: AnyObject {
    func useCorpusRecorder(_ recorder: CorpusRecorder)
}
