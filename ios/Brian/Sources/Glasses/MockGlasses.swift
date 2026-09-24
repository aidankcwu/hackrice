#if DEBUG
import Foundation
import UIKit

@MainActor
final class MockGlasses: GlassesSessioning, CaptureSenderConfigurable, CorpusRecordingConfigurable {
    private var sender: CapturePacketSender?
    private var recorder: CorpusRecorder?
    private var streamTask: Task<Void, Never>?

    private(set) var state: GlassesState = .connected {
        didSet {
            guard oldValue != state else { return }
            onStateChange?(state)
        }
    }
    var onStateChange: ((GlassesState) -> Void)?

    func useCaptureSender(_ sender: CapturePacketSender) {
        self.sender = sender
    }

    func useCorpusRecorder(_ recorder: CorpusRecorder) {
        self.recorder = recorder
    }

    func openMetaAI() {}
    func register() async throws { state = .connected }
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
                try? await Task.sleep(for: .seconds(1.5))
            }
        }
    }

    func stopStream() {
        streamTask?.cancel()
        streamTask = nil
    }

    private static func fixtureImage() -> UIImage? {
        UIImage(data: Data(base64Encoded:
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
        ) ?? Data())
    }
}
#endif
