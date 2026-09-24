// ios/Brian addition (app/native, N-001), additive only: read-only accessors below
// `backendConnected` that expose the sender's delivery counters and MacLink's last
// spoken line to AppState's ConnectionStatus / StreamStats. Nothing else changed.
import Foundation
import Observation
import UserNotifications

@MainActor
@Observable
final class Link {
    let macLink: MacLink
    let sender: CapturePacketSender
    var listener: QuestionListener { macLink.listener }
    var sensors: PhoneSensors { sender.sensors }

    private weak var session: (any GlassesSessioning)?
    private var transportTask: Task<Void, Never>?
    @ObservationIgnored private let recorder = CorpusRecorder()

    init() {
        self.macLink = MacLink()
        self.sender = CapturePacketSender()
        recorder.isRecording = false
        wireWhisperRouting()
        wireTerminalCallbacks()
    }

    init(macLink: MacLink, sender: CapturePacketSender) {
        self.macLink = macLink
        self.sender = sender
        wireWhisperRouting()
        wireTerminalCallbacks()
    }

    func configure(serverURL: String) {
        macLink.configure(serverURL: serverURL)
    }

    func connect() {
        macLink.connect()
        sender.isConnected = macLink.connected
    }

    func disconnect() {
        stop()
        macLink.disconnect()
        sender.isConnected = false
    }

    func start(session: GlassesSessioning) async throws {
        self.session = session
        (session as? CaptureSenderConfigurable)?.useCaptureSender(sender)
        (session as? CorpusRecordingConfigurable)?.useCorpusRecorder(recorder)
        connect()
        do {
            try await waitForConnection()
            sender.isConnected = true
            sender.start { [macLink] json, done in
                macLink.sendRaw(json, completion: done)
            }
            monitorTransport()
            try await session.startStream()
        } catch {
            stop()
            macLink.disconnect()
            throw error
        }
    }

    func stop() {
        session?.stopStream()
        transportTask?.cancel()
        transportTask = nil
        sender.stop()
        session = nil
    }

    var statusLine: String {
        "\(macLink.status) · \(sender.statusLine)"
    }

    var endpointLabel: String { macLink.endpointLabel }
    var accessDenied: Bool { macLink.accessDenied }
    var backendConnected: Bool { macLink.connected }
    /// Capture packets the transport confirmed, cumulative across sessions (N-001).
    var framesSent: Int { sender.sentCount }
    var lastFrameAt: Date? { sender.lastSentAt }
    var lastSpokenText: String? { macLink.lastSpokenText }
    var lastSpokenAt: Date? { macLink.lastSpokenAt }

    func setCorpusRecording(_ enabled: Bool) {
        recorder.isRecording = enabled
        (session as? CorpusRecordingConfigurable)?.useCorpusRecorder(recorder)
    }

    func sayTestLine() {
        macLink.send("Say this test line through the glasses.")
    }

    func postWhisperNotification(_ text: String) {
        let content = UNMutableNotificationContent()
        content.body = text
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "brian.whisper.\(UUID().uuidString)",
            content: content,
            trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    private func wireWhisperRouting() {
        macLink.shouldPlayWhispers = { [weak self] in
            guard let self else { return false }
            let voiceEnabled = UserDefaults.standard.object(forKey: "speakThroughGlasses") as? Bool ?? true
            return voiceEnabled && self.session?.state == .connected
        }
        macLink.onWhisperNotification = { [weak self] text in
            self?.postWhisperNotification(text)
        }
    }

    private func wireTerminalCallbacks() {
        macLink.onAccessDenied = { [weak self] in
            self?.stopForAccessDenied()
        }
    }

    /// A 4401 is terminal for this stream. Run this on the main actor before the app
    /// state callback so capture, DAT, and the visible Watching state cannot diverge.
    private func stopForAccessDenied() {
        session?.stopStream()
        transportTask?.cancel()
        transportTask = nil
        sender.stop()
        sender.isConnected = false
        session = nil
        onAccessDenied?()
    }

    /// Observed by AppState; set by it once during construction.
    var onAccessDenied: (() -> Void)?

    private func waitForConnection() async throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while !macLink.connected {
            if macLink.accessDenied { throw LinkError.accessDenied }
            guard ContinuousClock.now < deadline else { throw LinkError.connection }
            try await Task.sleep(for: .milliseconds(100))
        }
    }

    private func monitorTransport() {
        transportTask?.cancel()
        transportTask = Task { [weak self] in
            var wasConnected = false
            while !Task.isCancelled {
                guard let self else { return }
                let connected = self.macLink.connected
                self.sender.isConnected = connected
                if connected && !wasConnected && self.sender.isRunning {
                    self.sender.announce()
                }
                wasConnected = connected
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }
}

private enum LinkError: LocalizedError {
    case connection
    case accessDenied

    var errorDescription: String? {
        switch self {
        case .connection:
            return "The server did not connect. Check the link, then try Start watching again."
        case .accessDenied:
            return "This link no longer has access. Replace the link from your invite, then start watching again."
        }
    }
}
