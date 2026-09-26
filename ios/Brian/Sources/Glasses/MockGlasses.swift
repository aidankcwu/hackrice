#if DEBUG
import Foundation
import UIKit

@MainActor
final class MockGlasses: GlassesSessioning, CaptureSenderConfigurable, CorpusRecordingConfigurable {
    private var sender: CapturePacketSender?
    private var recorder: CorpusRecorder?
    private var streamTask: Task<Void, Never>?

    /// `.connected` only while the fixture frames flow, like GlassesSession.
    private(set) var state: GlassesState = .registered {
        didSet {
            guard oldValue != state else { return }
            onStateChange?(state)
        }
    }
    var onStateChange: ((GlassesState) -> Void)?
    private(set) var deviceState: GlassesDeviceState? = .demo
    var onDeviceStateChange: ((GlassesDeviceState?) -> Void)?
    var onPreviewFrame: ((UIImage, Date) -> Void)?
    /// No hardware to reject a registration, so this is stored and never called.
    var onRegistrationFailure: ((String) -> Void)?

    func useCaptureSender(_ sender: CapturePacketSender) {
        self.sender = sender
    }

    func useCorpusRecorder(_ recorder: CorpusRecorder) {
        self.recorder = recorder
    }

    func openMetaAI() {}
    func register() async throws {}
    func handleIncomingURL(_ url: URL) async {}

    func startStream() async throws {
        guard streamTask == nil else { return }
        state = .connected
        streamTask = Task { [weak self] in
            while !Task.isCancelled {
                if let image = Self.fixtureImage() {
                    self?.sender?.offer(image, at: Date())
                    self?.recorder?.offer(image)
                }
                // The sender gets the 1-pixel frame; the Preview sheet a picture a person can read.
                if let preview = PreviewFeed.fixtureImage() {
                    self?.onPreviewFrame?(preview, Date())
                }
                try? await Task.sleep(for: .seconds(1.5))
            }
        }
    }

    func stopStream() {
        streamTask?.cancel()
        streamTask = nil
        state = .registered
    }

    private static func fixtureImage() -> UIImage? {
        UIImage(data: Data(base64Encoded:
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
        ) ?? Data())
    }
}
#endif
