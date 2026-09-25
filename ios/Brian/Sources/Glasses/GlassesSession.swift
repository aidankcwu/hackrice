import Foundation
import MWDATCamera
import MWDATCore
import UIKit

@MainActor
final class GlassesSession: GlassesSessioning, CaptureSenderConfigurable, CorpusRecordingConfigurable {
    private let wearables: WearablesInterface
    private let deviceSelector: AutoDeviceSelector
    private var deviceSession: DeviceSession?
    private var camera: MWDATCamera.Camera?
    private var sender: CapturePacketSender?
    private var recorder: CorpusRecorder?
    private var registrationTask: Task<Void, Never>?
    private var deviceTask: Task<Void, Never>?
    private let sessionTokens = ListenerTokenBag()
    private let streamTokens = ListenerTokenBag()
    private let deviceStateTokens = ListenerTokenBag()
    /// The device whose state `deviceState` follows: the first one DAT lists.
    private var observedDevice: DeviceIdentifier?

    private(set) var state: GlassesState = .notRegistered {
        didSet {
            guard oldValue != state else { return }
            onStateChange?(state)
        }
    }
    var onStateChange: ((GlassesState) -> Void)?

    private(set) var deviceState: GlassesDeviceState? = nil {
        didSet {
            guard oldValue != deviceState else { return }
            onDeviceStateChange?(deviceState)
        }
    }
    var onDeviceStateChange: ((GlassesDeviceState?) -> Void)?
    var onPreviewFrame: ((UIImage, Date) -> Void)?

    convenience init() {
        try? Wearables.configure()
        self.init(wearables: Wearables.shared)
    }

    init(wearables: WearablesInterface) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
        updateAvailability(devices: wearables.devices, registration: wearables.registrationState)
        observeDeviceState(devices: wearables.devices)
        registrationTask = Task { [weak self] in
            guard let self else { return }
            for await registration in wearables.registrationStateStream() {
                self.updateAvailability(devices: self.wearables.devices, registration: registration)
            }
        }
        deviceTask = Task { [weak self] in
            guard let self else { return }
            for await devices in wearables.devicesStream() {
                self.updateAvailability(devices: devices, registration: self.wearables.registrationState)
                self.observeDeviceState(devices: devices)
            }
        }
    }

    isolated deinit {
        registrationTask?.cancel()
        deviceTask?.cancel()
        deviceStateTokens.clear()
        deviceSession?.stop()
    }

    func useCaptureSender(_ sender: CapturePacketSender) {
        self.sender = sender
    }

    func useCorpusRecorder(_ recorder: CorpusRecorder) {
        self.recorder = recorder
    }

    func openMetaAI() {
        guard let url = URL(string: "fb-viewapp://") else { return }
        UIApplication.shared.open(url)
    }

    func register() async throws {
        do {
            try await wearables.startRegistration()
        } catch {
            throw GlassesSessionError.registration
        }
    }

    func handleIncomingURL(_ url: URL) async {
        do {
            _ = try await wearables.handleUrl(url)
            // The registration stream normally delivers this update. Reflect the
            // synchronous state as well, so Setup changes on the callback even before
            // a device availability refresh arrives.
            if wearables.registrationState == .registered, state != .connected {
                state = .registered
            } else {
                updateAvailability(devices: wearables.devices, registration: wearables.registrationState)
            }
        } catch {
            // A URL for another integration is not a user-visible registration failure.
        }
    }

    func startStream() async throws {
        guard camera == nil else { return }
        guard wearables.registrationState == .registered else {
            throw GlassesSessionError.registration
        }
        do {
            let session: DeviceSession
            if let existing = deviceSession, existing.state == .started {
                session = existing
            } else {
                session = try wearables.createSession(deviceSelector: deviceSelector)
                deviceSession = session
                observeSession(session)
                try session.start()
                for await sessionState in session.stateStream() {
                    if sessionState == .started { break }
                    if sessionState == .stopped { throw GlassesSessionError.connection }
                }
            }

            if try await wearables.checkPermissionStatus(.camera) != .granted {
                guard try await wearables.requestPermission(.camera) == .granted else {
                    throw GlassesSessionError.permission
                }
            }
            let config = StreamConfiguration(
                videoCodec: .raw,
                resolution: .medium,
                frameRate: 2
            )
            guard let newCamera = try session.addCamera(config: config) else {
                throw GlassesSessionError.connection
            }
            camera = newCamera
            observeStream(newCamera.stream)
            newCamera.stream.start()
        } catch let error as GlassesSessionError {
            throw error
        } catch {
            throw GlassesSessionError.connection
        }
    }

    func stopStream() {
        camera?.stop()
        clearStream()
        deviceSession?.stop()
        sessionTokens.clear()
        deviceSession = nil
        updateAvailability(devices: wearables.devices, registration: wearables.registrationState)
    }

    private func observeSession(_ session: DeviceSession) {
        session.statePublisher.listen { [weak self] sessionState in
            Task { @MainActor in
                guard let self else { return }
                if sessionState == .stopped {
                    self.sessionTokens.clear()
                    self.deviceSession = nil
                    self.clearStream()
                    self.updateAvailability(
                        devices: self.wearables.devices,
                        registration: self.wearables.registrationState)
                }
            }
        }.store(in: sessionTokens)
    }

    private func observeStream(_ stream: MWDATCamera.Stream) {
        stream.statePublisher.listen { [weak self] streamState in
            Task { @MainActor in
                guard let self else { return }
                switch streamState {
                case .streaming:
                    self.state = .connected
                case .stopped:
                    self.clearStream()
                    self.updateAvailability(
                        devices: self.wearables.devices,
                        registration: self.wearables.registrationState)
                default:
                    break
                }
            }
        }.store(in: streamTokens)
        stream.videoFramePublisher.listen { [weak self] frame in
            guard let image = frame.makeUIImage() else { return }
            let at = Date()
            Task { @MainActor [weak self] in
                self?.sender?.offer(image, at: at)
                self?.recorder?.offer(image)
                self?.onPreviewFrame?(image, at)
            }
        }.store(in: streamTokens)
    }

    /// DAT "Device state": resolve the listed device and follow its battery and worn
    /// state. The listener fires at once with the current snapshot and on every change.
    private func observeDeviceState(devices: [DeviceIdentifier]) {
        let first = devices.first
        guard first != observedDevice else { return }
        deviceStateTokens.clear()
        observedDevice = first
        deviceState = nil
        guard let first, let device = wearables.deviceForIdentifier(first) else { return }
        device.addDeviceStateListener { [weak self] snapshot in
            let mapped = Self.deviceState(from: snapshot)
            Task { @MainActor in self?.deviceState = mapped }
        }.store(in: deviceStateTokens)
    }

    /// Values are stale once the link drops, so a disconnected snapshot reads as nil.
    nonisolated static func deviceState(from snapshot: DeviceState) -> GlassesDeviceState? {
        guard snapshot.linkState == .connected else { return nil }
        return GlassesDeviceState(
            batteryLevel: snapshot.batteryLevel,
            charging: snapshot.chargingState == .charging,
            worn: snapshot.donState == .unknown ? nil : snapshot.donState == .donned)
    }

    private func clearStream() {
        streamTokens.clear()
        camera = nil
    }

    private func updateAvailability(devices: [DeviceIdentifier], registration: RegistrationState) {
        if state == .connected { return }
        if registration != .registered {
            state = .notRegistered
        } else if devices.isEmpty {
            state = .unavailable
        } else {
            state = .registered
        }
    }
}

private enum GlassesSessionError: LocalizedError {
    case registration
    case permission
    case connection

    var errorDescription: String? {
        switch self {
        case .registration:
            return "Open Meta AI and turn on Developer Mode, then tap Register."
        case .permission:
            return "Open Meta AI, allow camera access for Zeroist, then tap Start again."
        case .connection:
            return "Put on and unfold your glasses, then tap Start again."
        }
    }
}
