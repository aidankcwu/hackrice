// The glasses layer without hardware: which DAT errors become which sentence, when
// `.connected` holds (only while frames flow), and that a deadline gives up on a DAT call
// that never answers.
import Foundation
import MWDATCamera
import MWDATCore
import Testing
@testable import Brian

@MainActor
struct GlassesErrorsTests {
    // MARK: - Registration

    @Test func alreadyRegisteredIsSuccess() {
        #expect(GlassesSessionError.forRegistration(RegistrationError.alreadyRegistered) == nil)
    }

    @Test func registrationErrorsMapToTheirSentence() {
        #expect(GlassesSessionError.forRegistration(RegistrationError.configurationInvalid) == .notConfigured)
        #expect(GlassesSessionError.forRegistration(RegistrationError.metaAINotInstalled) == .metaAINotInstalled)
        #expect(GlassesSessionError.forRegistration(RegistrationError.networkUnavailable) == .offline)
        #expect(GlassesSessionError.forRegistration(RegistrationError.unknown) == .registration)
        #expect(GlassesSessionError.forRegistration(URLError(.unknown)) == .registration)
        #expect(GlassesSessionError.forRegistration(GlassesSessionError.registrationTimeout) == .registrationTimeout)
    }

    @Test func registrationSentences() {
        #expect(GlassesSessionError.registrationTimeout.errorDescription
                == "Meta AI did not answer. Open Meta AI, make sure your glasses show as connected, then tap Register again.")
        #expect(GlassesSessionError.notConfigured.errorDescription
                == "This build of Zeroist is missing its Meta setup. Ask the Zeroist team for a new build.")
        #expect(GlassesSessionError.registration.errorDescription?.contains("release channel") == true)
    }

    // MARK: - Starting the stream

    @Test func batteryAndHeatGetTheirOwnSentence() {
        #expect(GlassesSessionError.forStart(DeviceSessionError.batteryCritical) == .batteryLow)
        #expect(GlassesSessionError.forStart(DeviceSessionError.peakPowerShutdown) == .batteryLow)
        #expect(GlassesSessionError.forStart(DeviceSessionError.thermalCritical) == .tooWarm)
        #expect(GlassesSessionError.forStart(DeviceSessionError.thermalEmergency) == .tooWarm)
        #expect(GlassesSessionError.batteryLow.errorDescription
                == "Your glasses battery is too low. Charge them in the case, then try again.")
        #expect(GlassesSessionError.tooWarm.errorDescription
                == "Your glasses are too warm. Let them cool for a few minutes, then try again.")
    }

    @Test func updatesAndOldBuilds() {
        #expect(GlassesSessionError.forStart(DeviceSessionError.datAppOnTheGlassesUpdateRequired) == .glassesUpdate)
        #expect(GlassesSessionError.forStart(DeviceSessionError.insufficientSDKVersion) == .buildTooOld)
    }

    @Test func everythingElseMeansTheGlassesDidNotRespond() {
        #expect(GlassesSessionError.forStart(DeviceSessionError.noEligibleDevice) == .connection)
        #expect(GlassesSessionError.forStart(DeviceSessionError.dwaUnavailable) == .connection)
        #expect(GlassesSessionError.forStart(DeviceSessionError.unexpectedError(description: "x")) == .connection)
        #expect(GlassesSessionError.forStart(PermissionError.noDeviceWithConnection) == .connection)
        #expect(GlassesSessionError.forStart(URLError(.timedOut)) == .connection)
        #expect(GlassesSessionError.connection.errorDescription
                == "Your glasses did not respond. Take them out of the case, unfold them, put them on, and tap Start watching again.")
    }

    @Test func cameraPermission() {
        #expect(GlassesSessionError.forStart(PermissionError.requestTimeout) == .permission)
        #expect(GlassesSessionError.forStart(PermissionError.requestInProgress) == .permission)
        #expect(GlassesSessionError.forStart(PermissionError.metaAINotInstalled) == .metaAINotInstalled)
        #expect(GlassesSessionError.forStart(GlassesSessionError.permission) == .permission)
        #expect(GlassesSessionError.permission.errorDescription
                == "Zeroist needs camera access. In Meta AI, allow Zeroist to use the glasses camera, then tap Start watching again.")
    }

    @Test func everySentenceIsPlain() {
        let all: [GlassesSessionError] = [
            .notConfigured, .registration, .registrationTimeout, .metaAINotInstalled, .offline,
            .connection, .permission, .batteryLow, .tooWarm, .glassesUpdate, .buildTooOld,
        ]
        for error in all {
            let sentence = error.errorDescription ?? ""
            #expect(sentence.hasSuffix("."), "\(error)")
            #expect(!sentence.contains("!"), "\(error)")
        }
    }

    // MARK: - `.connected` only while frames flow

    @Test func connectedNeedsAStreamThatIsStreaming() {
        #expect(GlassesSession.availability(registered: true, hasDevice: true, streaming: true) == .connected)
        #expect(GlassesSession.availability(registered: true, hasDevice: true, streaming: false) == .registered)
        // A Bluetooth drop or lost registration while streaming leaves `.connected` too.
        #expect(GlassesSession.availability(registered: true, hasDevice: false, streaming: true) == .unavailable)
        #expect(GlassesSession.availability(registered: false, hasDevice: true, streaming: true) == .notRegistered)
    }

    @Test func onlyStreamingCarriesFrames() {
        #expect(GlassesSession.framesFlow(.streaming))
        for state: StreamState in [.paused, .stopping, .stopped, .waitingForDevice, .starting] {
            #expect(!GlassesSession.framesFlow(state), "\(state)")
        }
    }

    @Test func mockGlassesLeaveConnectedOnStop() async throws {
        #if DEBUG
        let mock = MockGlasses()
        var seen: [GlassesState] = []
        mock.onStateChange = { seen.append($0) }
        #expect(mock.state != .connected)
        try await mock.startStream()
        #expect(mock.state == .connected)
        mock.stopStream()
        #expect(mock.state == .registered)
        #expect(seen == [.connected, .registered])
        #endif
    }

    @Test func unconfiguredBuildSaysSoOnRegisterAndStart() async {
        let glasses = UnconfiguredGlasses()
        await #expect(throws: GlassesSessionError.notConfigured) { try await glasses.register() }
        await #expect(throws: GlassesSessionError.notConfigured) { try await glasses.startStream() }
        #expect(glasses.state == .notRegistered)
    }

    // MARK: - Deadlines

    @Test func deadlinePassesAFastAnswerThrough() async throws {
        let value = try await GlassesSession.withDeadline(seconds: 5, orThrow: GlassesSessionError.connection) {
            42
        }
        #expect(value == 42)
    }

    @Test func deadlinePassesTheOperationsErrorThrough() async {
        await #expect(throws: GlassesSessionError.permission) {
            try await GlassesSession.withDeadline(seconds: 5, orThrow: GlassesSessionError.connection) {
                throw GlassesSessionError.permission
            }
        }
    }

    /// DAT calls may ignore cancellation; the deadline must not wait for them.
    @Test func deadlineGivesUpOnACallThatIgnoresCancellation() async {
        let started = Date()
        await #expect(throws: GlassesSessionError.connection) {
            try await GlassesSession.withDeadline(seconds: 0.2, orThrow: GlassesSessionError.connection) {
                await withUnsafeContinuation { (done: UnsafeContinuation<Void, Never>) in
                    DispatchQueue.global().asyncAfter(deadline: .now() + 3) { done.resume() }
                }
                return 1
            }
        }
        #expect(Date().timeIntervalSince(started) < 2)
    }
}
