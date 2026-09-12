# iOS side

The Xcode project lives **outside this repo** (`~/meta-wearables-dat-ios`, Meta's
`facebook/meta-wearables-dat-ios` sample). PERSON_A.md warns that nothing in it is
version-controlled alongside the Python, so this directory is the backup.

## What's here

| File | Task | Notes |
|---|---|---|
| `CorpusRecorder.swift` | A4 | 1 Hz, 512 px, q70 corpus writer. `encode()` is what A12 needs. |
| `MacLink.swift` | A11, A16 | WebSocket to the Mac + the `speak` handler. |
| `xcode-project.patch` | all | Every change to the sample, as a diff against upstream. |

The two `.swift` files are the standalone classes. The **patch** is what matters if the
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
- Do not open the glasses microphone. §22 warns it can knock iOS off A2DP and degrade
  the speaker path that A16 depends on.
- Free provisioning expires after 7 days.
