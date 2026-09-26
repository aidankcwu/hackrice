// The Meta DAT implementation of GlassesSessioning. `state` is `.connected` only while the
// camera stream reports `.streaming`: any stop or pause (glasses off or folded, touchpad,
// battery, heat, Bluetooth, stopStream) leaves it. startStream gives up on a deadline
// instead of waiting on the glasses or Meta AI forever, and every failure reaches the
// person as a GlassesSessionError sentence.
import Foundation
import MWDATCamera
import MWDATCore
import os
import UIKit

@MainActor
final class GlassesSession: GlassesSessioning, CaptureSenderConfigurable, CorpusRecordingConfigurable {
    /// The glasses get this long to start a session and answer the camera permission check.
    nonisolated static let startDeadline: Double = 20
    /// A trip to Meta AI (registration, the camera permission prompt): the person has to
    /// switch apps, decide, and come back.
    nonisolated static let metaAIDeadline: Double = 90
    nonisolated private static let log = Logger(subsystem: "com.zeroist.app", category: "glasses")

    private let wearables: WearablesInterface
    private let deviceSelector: AutoDeviceSelector
    private var deviceSession: DeviceSession?
    private var camera: MWDATCamera.Camera?
    /// True only while the camera stream reports `.streaming`: the one source of `.connected`.
    private var streaming = false
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
    var onRegistrationFailure: ((String) -> Void)?

    /// The session the app runs on. DAT must not be touched once `Wearables.configure()`
    /// has failed (the build lacks its Meta setup), so a stand-in says so on Register.
    static func live() -> GlassesSessioning {
        do {
            try Wearables.configure()
        } catch WearablesError.alreadyConfigured {
            // Configured earlier in this launch.
        } catch {
            log.error("Wearables.configure failed: \(String(describing: error), privacy: .public)")
            return UnconfiguredGlasses()
        }
        return GlassesSession(wearables: Wearables.shared)
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
        let wearables = self.wearables
        do {
            try await Self.withDeadline(seconds: Self.metaAIDeadline,
                                        orThrow: GlassesSessionError.registrationTimeout) {
                try await wearables.startRegistration()
            }
        } catch {
            guard let mapped = GlassesSessionError.forRegistration(error) else {
                // Already registered: nothing for the person to fix.
                updateAvailability(devices: wearables.devices, registration: wearables.registrationState)
                return
            }
            Self.log.error("Registration failed: \(String(describing: error), privacy: .public)")
            throw mapped
        }
    }

    /// `wearables.handleUrl` answers `false`, without throwing, for a URL that is not for
    /// this integration: nothing to tell the person. A throw means DAT recognized the URL
    /// as a registration (or unregistration) attempt and it failed -- e.g. Meta AI's
    /// release channel is not selected -- which is exactly the case that used to be silent.
    func handleIncomingURL(_ url: URL) async {
        do {
            let handled = try await wearables.handleUrl(url)
            guard handled else {
                Self.log.info("handleUrl: URL was not for this integration")
                return
            }
            // The registration stream normally delivers this update. Reflect the
            // synchronous state as well, so Setup changes on the callback even before
            // a device availability refresh arrives.
            if wearables.registrationState == .registered, state != .connected {
                state = .registered
            } else {
                updateAvailability(devices: wearables.devices, registration: wearables.registrationState)
            }
        } catch {
            Self.log.error("handleUrl rejected a registration: \(String(describing: error), privacy: .public)")
            let sentence = GlassesSessionError.forRegistration(error) ?? .registration
            onRegistrationFailure?(sentence.errorDescription ?? "")
        }
    }

    func startStream() async throws {
        // Idempotent only while frames flow. A paused or stalled camera (the `.raw` codec
        // pauses whenever the app is backgrounded) would otherwise make recovery's restart
        // a no-op, so tear it down and start fresh.
        if camera != nil {
            if streaming { return }
            stopStream()
        }
        guard wearables.registrationState == .registered else {
            throw GlassesSessionError.registration
        }
        var attempt: DeviceSession?
        do {
            let session = try beginSession()
            attempt = session
            let wearables = self.wearables
            // One budget for the glasses to answer: the session starting, then the check.
            let granted = try await Self.withDeadline(seconds: Self.startDeadline,
                                                      orThrow: GlassesSessionError.connection) {
                try await Self.waitUntilStarted(session)
                return try await wearables.checkPermissionStatus(.camera) == .granted
            }
            if !granted {
                let status = try await Self.withDeadline(seconds: Self.metaAIDeadline,
                                                         orThrow: GlassesSessionError.permission) {
                    try await wearables.requestPermission(.camera)
                }
                guard status == .granted else { throw GlassesSessionError.permission }
            }
            // stopStream ran while this waited.
            guard deviceSession === session else { throw GlassesSessionError.connection }
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
        } catch {
            Self.log.error("Stream did not start: \(String(describing: error), privacy: .public)")
            // Leave no half-started session behind for the next tap, but only clean up what
            // this attempt owns: a stale attempt (Stop, then a new Start, while this one
            // waited) must not tear down the newer session.
            if attempt == nil || deviceSession === attempt { stopStream() }
            throw GlassesSessionError.forStart(error)
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

    /// Reuses a started session; otherwise stops whatever is left and starts a new one.
    private func beginSession() throws -> DeviceSession {
        if let existing = deviceSession, existing.state == .started { return existing }
        deviceSession?.stop()
        sessionTokens.clear()
        let session = try wearables.createSession(deviceSelector: deviceSelector)
        deviceSession = session
        observeSession(session)
        try session.start()
        return session
    }

    private func observeSession(_ session: DeviceSession) {
        session.statePublisher.listen { [weak self] sessionState in
            Task { @MainActor in
                guard let self, self.deviceSession === session else { return }
                switch sessionState {
                case .stopped:
                    self.sessionTokens.clear()
                    self.deviceSession = nil
                    self.clearStream()
                case .started:
                    // Back from a pause: the stream may not announce itself again.
                    self.streaming = self.camera.map { Self.framesFlow($0.stream.state) } ?? false
                case .idle, .starting, .paused, .stopping:
                    self.streaming = false
                }
                self.updateAvailability(
                    devices: self.wearables.devices,
                    registration: self.wearables.registrationState)
            }
        }.store(in: sessionTokens)
    }

    private func observeStream(_ stream: MWDATCamera.Stream) {
        stream.statePublisher.listen { [weak self] streamState in
            Task { @MainActor in
                guard let self, self.camera?.stream === stream else { return }
                self.streaming = Self.framesFlow(streamState)
                if streamState == .stopped { self.clearStream() }
                self.updateAvailability(
                    devices: self.wearables.devices,
                    registration: self.wearables.registrationState)
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
        streaming = false
    }

    private func updateAvailability(devices: [DeviceIdentifier], registration: RegistrationState) {
        state = Self.availability(registered: registration == .registered,
                                  hasDevice: !devices.isEmpty,
                                  streaming: streaming)
    }

    /// `.connected` means frames are flowing (the GlassesSessioning contract), so it needs
    /// a registered app, a listed device and a stream that is streaming right now.
    nonisolated static func availability(registered: Bool, hasDevice: Bool, streaming: Bool) -> GlassesState {
        if !registered { return .notRegistered }
        if !hasDevice { return .unavailable }
        return streaming ? .connected : .registered
    }

    /// Only `.streaming` delivers frames. `.paused` (glasses taken off or folded, touchpad
    /// pause, battery or heat) and `.waitingForDevice` do not.
    nonisolated static func framesFlow(_ streamState: StreamState) -> Bool {
        streamState == .streaming
    }

    /// Waits for DAT to report the session started. A session error does not end the wait
    /// on its own (some are warnings); if the session stops instead, the last one says why.
    nonisolated private static func waitUntilStarted(_ session: DeviceSession) async throws {
        if session.state == .started { return }
        let failure: Error? = await withTaskGroup(of: SessionStartEvent.self) { group in
            group.addTask {
                for await sessionState in session.stateStream() {
                    if sessionState == .started { return .started }
                    if sessionState == .stopped { return .stopped }
                }
                return .stopped
            }
            group.addTask {
                // `dwaOutOfStuRange` is a nonblocking compatibility warning.
                for await error in session.errorStream() where error != .dwaOutOfStuRange {
                    return .failed(error)
                }
                return .errorsEnded
            }
            var failure: Error = GlassesSessionError.connection
            for await event in group {
                switch event {
                case .started:
                    group.cancelAll()
                    return nil
                case .failed(let error):
                    failure = error
                case .stopped, .errorsEnded:
                    continue
                }
            }
            return failure
        }
        if let failure { throw failure }
    }

    /// Runs `operation`, giving up after `seconds` with `timeoutError`. DAT calls do not all
    /// honour cancellation, so this does not wait for the operation to wind down: it is
    /// cancelled, and whatever it returns later is dropped.
    nonisolated static func withDeadline<T: Sendable>(
        seconds: Double,
        orThrow timeoutError: any Error,
        _ operation: @escaping @Sendable () async throws -> T
    ) async throws -> T {
        let race = DeadlineRace<T>()
        return try await withCheckedThrowingContinuation { continuation in
            race.arm(continuation)
            race.add(Task {
                do {
                    race.finish(.success(try await operation()))
                } catch {
                    race.finish(.failure(error))
                }
            })
            race.add(Task {
                do {
                    try await Task.sleep(for: .seconds(seconds))
                } catch {
                    return  // the operation finished first
                }
                race.finish(.failure(timeoutError))
            })
        }
    }
}

private enum SessionStartEvent: Sendable {
    case started
    case stopped
    case failed(DeviceSessionError)
    case errorsEnded
}

/// Resumes a continuation once, from whichever racing task gets there first, then
/// cancels the others.
private final class DeadlineRace<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Error>?
    private var tasks: [Task<Void, Never>] = []

    func arm(_ continuation: CheckedContinuation<T, Error>) {
        lock.withLock { self.continuation = continuation }
    }

    func add(_ task: Task<Void, Never>) {
        let finished = lock.withLock {
            if continuation == nil { return true }
            tasks.append(task)
            return false
        }
        if finished { task.cancel() }
    }

    func finish(_ result: Result<T, Error>) {
        let (pending, others) = lock.withLock {
            defer { continuation = nil; tasks = [] }
            return (continuation, tasks)
        }
        guard let pending else { return }
        others.forEach { $0.cancel() }
        pending.resume(with: result)
    }
}

/// Every glasses failure the person sees, as a sentence they can act on.
enum GlassesSessionError: LocalizedError, Equatable {
    /// `Wearables.configure()` failed, or DAT calls the app's Meta configuration invalid.
    case notConfigured
    /// Meta AI would not register the app; for release-channel testers, usually the invite.
    case registration
    case registrationTimeout
    case metaAINotInstalled
    case offline
    /// The glasses did not start a session in time, or stopped while starting.
    case connection
    case permission
    case batteryLow
    case tooWarm
    case glassesUpdate
    case buildTooOld

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "This build of Zeroist is missing its Meta setup. Ask the Zeroist team for a new build."
        case .registration:
            return "Meta AI did not approve the connection. Check that you accepted the invite email and that Zeroist's release channel is selected in Meta AI under your glasses' settings, then tap Register again."
        case .registrationTimeout:
            return "Meta AI did not answer. Open Meta AI, make sure your glasses show as connected, then tap Register again."
        case .metaAINotInstalled:
            return "Install Meta AI from the App Store and pair your glasses in it, then try again."
        case .offline:
            return "Zeroist could not reach Meta. Check that your phone is online, then tap Register again."
        case .connection:
            return "Your glasses did not respond. Take them out of the case, unfold them, put them on, and tap Start watching again."
        case .permission:
            return "Zeroist needs camera access. In Meta AI, allow Zeroist to use the glasses camera, then tap Start watching again."
        case .batteryLow:
            return "Your glasses battery is too low. Charge them in the case, then try again."
        case .tooWarm:
            return "Your glasses are too warm. Let them cool for a few minutes, then try again."
        case .glassesUpdate:
            return "Your glasses need an update. Open Meta AI, go to App Connections and update them, then try again."
        case .buildTooOld:
            return "This build of Zeroist is too old for your glasses. Ask the Zeroist team for a new build."
        }
    }

    /// What `register()` shows for an error from `startRegistration()`. Nil when there is
    /// nothing to fix: DAT answers `alreadyRegistered` when this phone already is.
    static func forRegistration(_ error: Error) -> GlassesSessionError? {
        if let own = error as? GlassesSessionError { return own }
        guard let dat = error as? RegistrationError else { return .registration }
        switch dat {
        case .alreadyRegistered: return nil
        case .configurationInvalid: return .notConfigured
        case .metaAINotInstalled: return .metaAINotInstalled
        case .networkUnavailable: return .offline
        case .unknown: return .registration
        @unknown default: return .registration
        }
    }

    /// What `startStream()` shows for anything thrown while the session, the permission
    /// check or the camera starts.
    static func forStart(_ error: Error) -> GlassesSessionError {
        if let own = error as? GlassesSessionError { return own }
        if let session = error as? DeviceSessionError {
            switch session {
            case .batteryCritical, .peakPowerShutdown: return .batteryLow
            case .thermalCritical, .thermalEmergency: return .tooWarm
            case .datAppOnTheGlassesUpdateRequired: return .glassesUpdate
            case .insufficientSDKVersion: return .buildTooOld
            default: return .connection
            }
        }
        if let permission = error as? PermissionError {
            switch permission {
            case .requestInProgress, .requestTimeout: return .permission
            case .metaAINotInstalled: return .metaAINotInstalled
            default: return .connection
            }
        }
        return .connection
    }
}
