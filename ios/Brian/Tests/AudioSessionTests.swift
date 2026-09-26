// Empirical probe (docs/PERSON_A.md audio-route debug): Apple's header for
// AVAudioSession.CategoryOptions.allowBluetoothA2DP (AVAudioSessionTypes.h ~551-572) says
// the option is valid to *set* only alongside `.playAndRecord`; for every other category
// (including `.playback`) it is "always implicitly true and cannot be changed" when the
// route supports it. The existing "-50 trap" comments in MacLink.swift/QuestionListener.swift
// instead blame -50 on "a session already exists" (first written in 0cba3ab).
//
// Hypothesis under test: passing `.allowBluetoothA2DP` alongside `.playback` throws
// paramErr (-50), so the category is never actually applied and the session stays in the
// `.soloAmbient` default (silenced by the Ring/Silent switch and screen lock).
//
// Result on the iOS 26 simulator (this run): DISPROVEN. Neither call throws, and
// `.category` reads back `.playback` in both cases — see the assertions below, which
// pin that observed behavior down as a regression test. This is evidence about API
// validation on the simulator only; it does not prove anything about Bluetooth routing
// on a real device, which the simulator cannot exercise (hardware_software.md: physical
// iPhone only). See ios/Brian's task report for the full git log -S / docs trail.
import AVFoundation
import Testing
@testable import Brian

struct AudioSessionTests {
    @Test func playbackSpokenAudioWithAllowBluetoothA2DPOptionDoesNotThrowAndApplies() throws {
        let session = AVAudioSession.sharedInstance()
        var thrown: (any Error)?
        do {
            try session.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP])
        } catch {
            thrown = error
        }
        let nsError = thrown as NSError?
        print(
            "[AudioSessionTests] with .allowBluetoothA2DP: threw=\(thrown != nil)"
                + " domain=\(nsError?.domain ?? "-") code=\(nsError?.code ?? 0)"
                + " category=\(session.category.rawValue) options=\(session.categoryOptions.rawValue)"
        )
        // Observed on simulator: no throw at all (not even -50), and the category is
        // genuinely applied — contradicting the "paramErr, category never applied" theory.
        #expect(thrown == nil)
        #expect(session.category == .playback)
        #expect(session.categoryOptions.contains(.allowBluetoothA2DP))
    }

    @Test func playbackSpokenAudioWithNoOptionsDoesNotThrowAndApplies() throws {
        let session = AVAudioSession.sharedInstance()
        var thrown: (any Error)?
        do {
            try session.setCategory(.playback, mode: .spokenAudio, options: [])
        } catch {
            thrown = error
        }
        let nsError = thrown as NSError?
        print(
            "[AudioSessionTests] no options: threw=\(thrown != nil)"
                + " domain=\(nsError?.domain ?? "-") code=\(nsError?.code ?? 0)"
                + " category=\(session.category.rawValue) options=\(session.categoryOptions.rawValue)"
        )
        #expect(thrown == nil)
        #expect(session.category == .playback)
    }
}
