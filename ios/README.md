# iOS side

The Xcode project lives **outside this repo** (`~/meta-wearables-dat-ios`, Meta's
`facebook/meta-wearables-dat-ios` sample). PERSON_A.md warns that nothing in it is
version-controlled alongside the Python, so this directory is the backup.

## What's here

| File | Task | Notes |
|---|---|---|
| `CorpusRecorder.swift` | A4 | 1 Hz, 512 px, q70 corpus writer. `encode()` is what A12 needs. |
| `MacLink.swift` | A11, A16 | WebSocket to the Mac + the `speak` handler. |
| `CapturePacketSender.swift` | A12–A14 | Sample, encode, send one capture packet; drops, never queues (`CapturePacketSender.swift:48`). Hello advertises `caps: ["ask"]` (`:408-413`). |
| `PhoneSensors.swift` | A13 | Raw accelerometer burst + GPS speed, no arithmetic on the phone (`PhoneSensors.swift:35,74,81`). |
| `QuestionListener.swift` | ask/answer | One bounded glasses-mic answer window, on-device STT, restores A2DP (`QuestionListener.swift:161-200,244-260`). See "Glasses microphone". |
| `xcode-project.patch` | all | Every change to the sample, as a diff against upstream. |

The `.swift` files are the standalone classes. The **patch** is what matters if the
project is ever lost or rebuilt, because it also carries the three integration edits
that live nowhere else:

- `Info.plist` — `NSAppTransportSecurity` -> `NSAllowsLocalNetworking`. Without it a
  plain `ws://` fails **silently** and reads exactly like a backend bug (PERSON_A.md A11).
  (`NSLocalNetworkUsageDescription` was already in the sample.)
- `CameraAccess.entitlements` — Access Wi-Fi Information and Hotspot Configuration
  **removed**; a free Personal Team cannot provision them (hardware_software.md §7, §26).
  Do not add them back.
- `CameraViewModel.swift` — `corpusRecorder` property plus the hook in the DAT frame
  publisher that feeds it every decoded frame.
- `CameraView.swift` — the Record corpus / Connect to Mac buttons and `MacLink` state.
- `project.pbxproj` — the two new files added to the `CameraAccess` target.

## Applying the patch to a fresh clone

```
git clone https://github.com/facebook/meta-wearables-dat-ios
cd meta-wearables-dat-ios
git checkout 225f64f          # the upstream commit this patch was cut against
git apply /path/to/hackrice/ios/xcode-project.patch
```

Then in Xcode: set the bundle identifier to something unique and pick your Personal
Team under Signing & Capabilities (the sample's identifier will not sign for you).

## Regenerating the patch after more Swift work

```
cd ~/meta-wearables-dat-ios
git add -N samples/CameraAccess/*.swift
git diff HEAD -- samples/ > ~/hackrice/ios/xcode-project.patch
git reset -q -- samples/
```

Worth doing after A12–A14 land, since that is the Swift most worth not losing.

## Gotchas that cost time

- Physical iPhone only — the simulator cannot do DAT (§5.3).
- DAT availability is transient. "Device unavailable" and CoreBluetooth `API MISUSE`
  logs do not mean the build is broken; power-cycling the glasses has fixed it (§24, §25).
- `AVAudioSession.setCategory` throws `OSStatus -50` when the app already holds a
  configured session. **Ignore it and speak anyway** — the sample's validated
  `speakTest()` does. Returning early on it silently swallows every utterance.
- The glasses microphone is scoped, not banned: only the ask/answer window may open it.
  The rule, the reason and what is still unmeasured are under "Glasses microphone" below.
- Free provisioning expires after 7 days.

## Glasses microphone

**Rule.** Only `QuestionListener` opens the glasses microphone, and only for one answer
window: after the question's playback has finished (`MacLink.swift:463-478,508-519`), for
`listen_s` seconds or until a 1.2 s pause (`QuestionListener.swift:195-199,210-216`). Never
hold it open, never listen continuously, never set `.playAndRecord` anywhere else. Every
exit (answer, timeout, interruption, device lost, cancel, socket failure) must restore
`.playback` / `.spokenAudio` / A2DP (`QuestionListener.swift:244-260`).

**Why.** A Bluetooth microphone can move iOS off output-only A2DP onto the hands-free
profile (`hardware_software.md:646-664`, §22). `.playAndRecord` + `.allowBluetoothHFP`
(`QuestionListener.swift:172-174`) does exactly that, so anything played while the window
is open sounds like a phone call, and the speaker path A16 depends on stays degraded until
the restore runs (`XCODE_ASK.md:97-106`). The Mac stays quiet for the window: a `speak` is
dropped while a question is listening (`backend/pipeline/actions/handlers.py:252-253`,
`docs/ASK_DESIGN.md:169-171`).

**History.** The old absolute rule ("do not open the glasses microphone") was written in
2efebc1 (2026-09-12 04:17), before ask/answer existed. Ask/answer landed in adffe24 the
same day (15:32) and opens the mic on purpose (`docs/ASK_DESIGN.md:8-13,29`,
`ios/INTEGRATION.md:217-223`). The new app declares the same use
(`ios/Brian/project.yml:56-57`).

**Not yet measured.** §22 never validated DAT camera input with a live glasses mic
(`hardware_software.md:660-664`). `XCODE_ASK.md:86-95` asks for switch latency, the input
route (`BluetoothHFP`) and whether DAT frames keep arriving during the window, recorded in
`FINDINGS.md` (`XCODE_ASK.md:157-158`); `FINDINGS.md` has no such section yet. Until it
does, "DAT keeps streaming while the mic is open" is unknown. If the HFP switch kills DAT,
the fallback is the phone mic: drop `.allowBluetoothHFP` (`XCODE_ASK.md:173,176`).
