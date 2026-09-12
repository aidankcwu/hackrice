Ray-Ban Meta ↔ iPhone ↔ Computer Integration Context

Purpose

This document describes the hardware/software boundary and runtime interaction for the current Ray-Ban Meta project.

It is intended as context for a coding agent working primarily in VS Code on the main application logic while the glasses integration remains an iOS/Swift/Xcode component.

This document is limited to factual integration details: what runs where, what the Meta Device Access Toolkit (DAT) does, what Xcode is needed for, how camera frames reach the computer, and how audio reaches the Ray-Ban speakers.

1. System boundary

For regular Ray-Ban Meta glasses, the project has three distinct execution environments:

Ray-Ban Meta glasses
        │
        │ camera / device connection
        ▼
iPhone iOS app
  - Swift
  - Meta Wearables Device Access Toolkit (DAT)
  - AVFoundation / iOS Bluetooth audio
        │
        │ project-defined network transport
        ▼
Computer
  - main application logic
  - Python/backend/VLM/reasoning/storage/etc.

The critical boundary is:

Ray-Ban hardware ↔ iPhone Swift app ↔ computer application

The Meta DAT SDK is a mobile SDK. It is not a Python SDK running on the computer and it does not run user code on the glasses.

The computer does not directly open the Ray-Ban camera stream through DAT.

2. What runs on the Ray-Ban glasses

For this project, the regular Ray-Ban Meta glasses provide:

camera

microphones

speakers

device state / connectivity exposed through the supported Meta/iOS interfaces

The project does not upload its Python application, VLM, reasoner, database, or backend code to the glasses.

The glasses remain paired with the iPhone through the Meta AI companion app.

For regular Ray-Ban Meta Gen 1 / Gen 2:

camera access is exposed through the Meta Wearables Device Access Toolkit

microphone and speaker access are exposed through the normal iOS/Android Bluetooth audio path

There is no visual lens display on the regular Ray-Ban Meta model.

3. What runs on the iPhone

The iPhone is the software bridge between the glasses and the computer.

The iPhone-side application is a native iOS application written in Swift.

Its responsibilities at the hardware boundary are:

initialize the Meta Wearables SDK

register/connect the app through Meta AI

discover/select the glasses

create and maintain a DAT DeviceSession

create and maintain the camera stream

receive VideoFrame objects from DAT

convert frames into a form the rest of the project can transport/process

collect any iPhone sensor values used by the project

communicate with the computer using the project's own network layer

receive speech/audio actions from the computer

play audio through iOS so that the Ray-Ban Bluetooth speakers receive it

DAT does not provide the iPhone-to-computer transport. That transport belongs to the project itself.

4. What runs on the computer

The computer receives data only after the iPhone application sends it over the project's network connection.

The computer can contain the main application logic, including:

image processing

VLM calls

trigger logic

reasoning

persistent storage

scoring

dashboard/backend services

text-to-speech generation, if TTS is generated off-phone

From the computer's point of view, the Ray-Bans are not a directly attached camera device.

The computer sees whatever messages/images/audio metadata the iPhone bridge sends to it.

5. Xcode vs VS Code

5.1 VS Code

VS Code can be used to edit:

Python/backend code

frontend code

configuration files

Swift source files, if desired

Editing Swift in VS Code does not remove the need for the Apple iOS toolchain.

5.2 Xcode

Full Xcode must remain installed on the Mac because it supplies:

the iOS SDK

Swift/iOS build tooling

xcodebuild

code signing

provisioning

iPhone device deployment

Swift Package Manager integration used by the iOS project

target configuration

Info.plist target settings

Signing & Capabilities

iOS platform/device support

debugging/logging for the physical iPhone

The iOS app may be edited in VS Code, but it is still an Xcode project and is built/signed/deployed using Xcode's toolchain.

The command-line toolchain must point at the full Xcode installation rather than only Apple's standalone Command Line Tools.

The active developer directory should be:

/Applications/Xcode.app/Contents/Developer

Typical verification:

xcode-select -p
xcodebuild -version

If necessary, the full Xcode installation is selected with:

sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

5.3 Physical iPhone

The DAT camera integration is tested on a physical iPhone, not an iOS simulator.

The current development phone has:

iPhone Developer Mode enabled

Meta AI installed

Ray-Ban Meta glasses paired in Meta AI

Meta AI Developer Mode enabled

iOS 26.5

Xcode iOS 26.5 platform support installed on the Mac

6. Current validated Xcode project state

The working project is based on Meta's official iOS CameraAccess sample from:

facebook/meta-wearables-dat-ios

The sample has already been built and run successfully on the physical iPhone.

The current local project state has been validated to support:

successful app installation on the iPhone

registration/connection through Meta AI

successful DAT device session

live Ray-Ban camera preview

continuous video streaming

app-generated speech through the Ray-Ban speakers

camera streaming and speech playback at the same time

The original Meta sample bundle identifier was changed to a unique application bundle identifier so that the local Apple Personal Team could sign the app.

Automatic signing is enabled with the user's Apple Personal Team.

7. Apple signing/capability details encountered in this setup

The local app is signed using an Apple Personal Team.

During setup, the sample initially contained Apple capabilities that a free Personal Team could not provision:

Access Wi-Fi Information

Hotspot Configuration

Those capabilities were removed from the local target.

The working glasses camera path uses the external-accessory/Bluetooth connection rather than depending on those removed Wi-Fi/Hotspot entitlements.

The following capability is enabled in the target:

Background Modes
  - External accessory communication

The target also contains the supported external accessory protocol:

Supported external accessory protocols
  Item 0: com.meta.ar.wearable

In plist form, the protocol is:

<key>UISupportedExternalAccessoryProtocols</key>
<array>
    <string>com.meta.ar.wearable</string>
</array>

Meta's current iOS getting-started configuration also documents DAT-related target/plist entries including:

MWDATCore
MWDATCamera

UISupportedExternalAccessoryProtocols
  com.meta.ar.wearable

UIBackgroundModes
  bluetooth-peripheral
  external-accessory

NSBluetoothAlwaysUsageDescription

MWDAT
  AppLinkURLScheme
  MetaAppID

For Developer Mode, Meta's documentation uses:

MetaAppID = 0

The official sample already contains the registration/callback configuration required for Meta AI integration.

8. Meta SDK packages

The camera application uses the Meta Wearables Device Access Toolkit packages:

import MWDATCore
import MWDATCamera

MWDATCore covers the core wearables/device/session/registration functionality.

MWDATCamera covers camera streaming, video frames, and photo capture.

The current Meta repository documents the main SDK entry point as:

Wearables.shared

and initializes the SDK with:

Wearables.configure()

at application launch.

9. Meta AI registration and Developer Mode

The iPhone has the Meta AI companion app installed and the Ray-Ban glasses are paired through it.

DAT development requires Meta AI Developer Mode.

The application registration flow may hand off from the custom iOS app to Meta AI and back to the custom app.

The development configuration uses the Meta AI callback/app-link setup in the Xcode target/Info.plist.

Meta AI pairing and DAT app registration are separate from Apple's iPhone Developer Mode.

Both are already enabled in the current environment.

10. DAT device/session lifecycle

The camera stream is tied to a DAT device session.

The current Meta API model is:

Wearables.shared
      ↓
DeviceSelector
      ↓
DeviceSession
      ↓
start session
      ↓
wait until session state == started
      ↓
attach camera/stream capability
      ↓
start stream
      ↓
receive VideoFrame objects

The current SDK exposes device selectors such as:

AutoDeviceSelector
SpecificDeviceSelector

A session is created against a selector.

Conceptual Swift form:

let wearables = Wearables.shared
let selector = AutoDeviceSelector(wearables: wearables)
let session = try wearables.createSession(deviceSelector: selector)

try session.start()

for await state in session.stateStream() {
    if state == .started {
        break
    }
}

A camera/stream capability is attached only after the device session reaches the started state.

The official SDK/sample exposes asynchronous session and stream state rather than treating camera access as a single blocking call.

11. Camera stream configuration

DAT's StreamConfiguration controls the camera stream.

The current Meta iOS documentation lists these frame rates as valid:

2 FPS
7 FPS
15 FPS
24 FPS
30 FPS

The current documented resolution options are:

.low     = 360 × 640
.medium  = 504 × 896
.high    = 720 × 1280

The project architecture currently uses a DAT camera stream of:

2 FPS

while downstream processing may sample from that stream at a different cadence.

The DAT camera rate and the application's processing rate do not have to be identical.

12. Video frames

DAT delivers individual camera frames to the iOS application as VideoFrame values.

The current SDK provides a convenience conversion:

frame.makeUIImage()

The official sample uses a frame listener/publisher and updates its current preview image whenever a frame arrives.

Conceptually:

stream.videoFramePublisher.listen { frame in
    guard let image = frame.makeUIImage() else { return }

    // image is now available to the iOS application
}

Once a VideoFrame has reached the Swift application, it can be:

displayed

resized

JPEG encoded

sampled

stored temporarily

transmitted over the application's own network connection

No additional still-photo request is required merely to obtain periodic visual snapshots if the continuous stream is already active.

A program can retain the latest frame and sample that frame at its own cadence.

DAT separately supports explicit still-photo capture as a different camera operation.

13. Continuous streaming vs periodic processing

The glasses camera session and the computer processing pipeline are separate clocks.

For example, with a 2 FPS stream:

t = 0.0   frame
t = 0.5   frame
t = 1.0   frame
t = 1.5   frame
...

The iOS app may keep only the latest frame.

A 1 Hz consumer can therefore process approximately:

t = 0
t = 1
t = 2
...

without stopping/restarting the DAT session.

The DAT session/stream can remain active continuously while individual received frames are discarded or sampled independently.

14. Frame encoding boundary

The raw DAT frame representation is an iOS/SDK implementation detail.

The project does not need to expose raw camera buffer formats to the computer.

At the iPhone-to-computer boundary, a frame can be converted to a standard representation such as:

UIImage
→ resized image
→ JPEG bytes

If a frame is transmitted as JPEG, the computer receives ordinary encoded image data rather than a DAT VideoFrame.

The DAT-specific object exists only on the iPhone side.

15. iPhone-to-computer communication

DAT does not create a direct connection from the glasses to the computer.

The data path is:

Ray-Ban camera
    ↓
DAT
    ↓
Swift iOS app
    ↓
project network transport
    ↓
computer

The iPhone application must explicitly serialize/transmit whatever the computer needs.

Possible transmitted information includes:

encoded image bytes

timestamps

frame IDs

iPhone motion data

iPhone location/speed data

session/device state

acknowledgements

action results

The transport between iPhone and computer is independent of DAT.

It may use local-network or Internet networking provided by iOS.

If the iPhone connects directly to a service running on the Mac over the local network, iOS may require local-network permission/configuration such as:

NSLocalNetworkUsageDescription

depending on the transport/service-discovery mechanism.

App Transport Security also applies normally to iOS network traffic; DAT does not bypass it.

16. Phone sensors

iPhone sensor APIs are separate from DAT.

Examples:

CoreMotion
  accelerometer
  device motion

CoreLocation
  location
  GPS-derived speed

Those values can be combined with a glasses frame by the Swift application before transmission to the computer.

The iPhone does not expose a general public ambient-light/lux sensor API for arbitrary applications.

Any camera-derived brightness measurement is therefore distinct from a true iPhone ambient-light sensor measurement.

17. Computer-to-iPhone command path

Actions generated on the computer must be sent back to the iPhone through the project's own network transport.

DAT does not automatically forward Python/backend actions to the phone.

The logical path is:

computer logic
    ↓
project network message
    ↓
Swift iOS app
    ↓
iOS API
    ↓
Ray-Ban hardware output

The message itself can contain text, audio data, an audio URL, an action identifier, or another project-defined payload.

18. Ray-Ban speaker path

For regular Ray-Ban Meta glasses, speaker playback is not the inverse of the DAT camera stream.

The two paths are separate:

CAMERA INPUT

Ray-Ban
  ↓
DAT
  ↓
Swift app

AUDIO OUTPUT

Swift/iOS audio
  ↓
iOS Bluetooth audio route
  ↓
Ray-Ban speakers

Meta's current documentation states that regular Ray-Ban Meta microphone/speaker access uses the normal iOS/Android Bluetooth profiles.

The iOS application uses AVFoundation / AVAudioSession for audio output.

19. Validated audio configuration

The current test app has successfully used:

let audioSession = AVAudioSession.sharedInstance()

try audioSession.setCategory(
    .playback,
    mode: .spokenAudio,
    options: [.allowBluetoothA2DP]
)

try audioSession.setActive(true)

The test used:

AVSpeechSynthesizer
AVSpeechUtterance

to synthesize arbitrary application-generated speech.

That speech was heard through the Ray-Ban Meta speakers.

The same test also confirmed that speech playback and live DAT camera streaming can operate at the same time in the current device configuration.

20. Audio route ownership

iOS owns the active audio route.

An application can inspect the current output route using:

AVAudioSession.sharedInstance().currentRoute.outputs

A Ray-Ban output may appear as a Bluetooth output route.

The current app printed the active route during the speech test.

The user can also select Ray-Ban Meta as the iPhone audio output through iOS Control Center.

Route changes are standard iOS audio events and can be observed through:

AVAudioSession.routeChangeNotification

The Ray-Ban camera DAT connection and the iOS audio route are separate subsystems.

21. Text-to-speech location

Speech may be generated either on the iPhone or elsewhere.

If text is sent to the phone, iOS can synthesize it using:

AVSpeechSynthesizer

If encoded audio is generated on the computer/backend, the iPhone can receive and play that audio using normal iOS audio APIs.

In both cases, the final hardware path is:

iPhone audio system
    ↓
Bluetooth audio route
    ↓
Ray-Ban speakers

22. Glasses microphone

The regular Ray-Ban Meta microphone path uses Bluetooth audio rather than the camera VideoFrame API.

Microphone input is a separate audio-routing concern from:

DAT camera streaming

AVSpeechSynthesizer

speaker playback

Enabling/using a Bluetooth microphone can cause iOS to select a different Bluetooth audio profile than high-quality output-only A2DP.

The current hardware validation covered:

camera input + speaker output

It did not validate a simultaneous custom glasses-microphone input pipeline.

23. Session state and availability

DAT device/session state is dynamic.

The iOS application may encounter states such as:

no active device
waiting for device
session starting
session started
stream starting
streaming
paused/stopping
session ended

The official sample exposes these transitions explicitly in the UI.

A device/session can become unavailable because the glasses are not currently available to DAT, Bluetooth/device state changes, another operation takes control, or the current session is interrupted.

Session/stream state must therefore be treated as runtime state rather than as a one-time startup assumption.

24. Wear/active-device behavior observed

The CameraAccess sample uses active-device state when deciding whether the camera session can start.

During testing, the UI displayed:

Put on your glasses
Waiting for an active device

when DAT did not consider a device active.

The project also encountered:

Device unavailable

during session start even though the glasses audibly responded.

After device/session state recovered, the same build successfully started a session and streamed camera frames.

The current environment has therefore demonstrated that DAT availability can be transient even when normal Bluetooth/Meta AI pairing still exists.

25. CoreBluetooth/network log messages observed

During development, Xcode logged messages including:

API MISUSE: <CBCentralManager ...> can only accept this command while in the powered on state
XPC connection invalid

and earlier L2CAP/channel errors such as:

No known channel matching peer ...
Cannot find l2CAP channel closed ...

The project also logged network/QUIC messages during failed/transitioning sessions.

These logs did not necessarily mean the app build itself was invalid.

The current project subsequently reached a working DAT session and live stream without rewriting the application architecture.

A physical power-cycle of the glasses restored operation during one transient failure.

26. Wi-Fi/Hotspot capability distinction

The official CameraAccess sample can contain configuration related to higher-bandwidth/local networking depending on SDK/sample version.

In this local setup:

Apple Personal Team signing rejected Access Wi-Fi Information

Apple Personal Team signing rejected Hotspot Configuration

both were removed

External accessory communication was enabled

com.meta.ar.wearable was added as the supported external accessory protocol

camera streaming then worked on the physical glasses

Therefore the currently validated local setup must not assume that the removed Hotspot/Wi-Fi entitlements are present.

The iPhone can still separately use its ordinary Wi-Fi/network connection for communication with the computer.

The glasses-to-phone transport and phone-to-computer transport are separate links.

27. Important separation of links

There are two independent communication links:

LINK A — glasses ↔ phone

Ray-Ban Meta
    ↕
Meta DAT / external accessory / Bluetooth-related device connection
    ↕
Swift iPhone app

LINK B — phone ↔ computer

Swift iPhone app
    ↕
ordinary iOS networking
    ↕
computer application

The computer is not part of the DAT DeviceSession.

The iPhone is the endpoint of the DAT session.

28. Data ownership at each boundary

Ray-Ban → iPhone

DAT-specific objects:

Device
DeviceSession
StreamConfiguration
Stream
VideoFrame
PhotoData

These are Swift/iOS-side concerns.

iPhone → computer

Project-defined data:

JPEG/image bytes
timestamps
sensor values
frame IDs
device/session state

The computer should not need Meta SDK types.

Computer → iPhone

Project-defined actions:

text to speak
audio to play
control/action messages
acknowledgement IDs

iPhone → Ray-Ban speaker

Standard iOS audio playback through the selected Bluetooth route.

29. Current hardware validation status

The following have been directly validated on the actual device pair:

[PASS] Custom iOS app builds and runs on the physical iPhone.

[PASS] Custom app registers/connects through Meta AI Developer Mode.

[PASS] DAT recognizes the Ray-Ban Meta glasses.

[PASS] A DAT DeviceSession can reach the started state.

[PASS] The camera stream can start.

[PASS] Live Ray-Ban camera frames appear in the custom iPhone app.

[PASS] Continuous video streaming works.

[PASS] Frames can therefore be sampled from the live stream without taking a
       separate still photo for every sample.

[PASS] The custom app can synthesize arbitrary text with AVSpeechSynthesizer.

[PASS] Generated speech can be heard through the Ray-Ban Meta speakers.

[PASS] Live DAT camera streaming and Ray-Ban speaker playback work simultaneously.

This means the hardware path already demonstrated is:

Ray-Ban camera
    ↓
DAT
    ↓
Swift iPhone app

and simultaneously:

Swift/iOS generated audio
    ↓
Bluetooth audio
    ↓
Ray-Ban speakers

30. Current source file modified for the audio test

The working Meta CameraAccess sample contains:

CameraAccess
└── Views
    └── CameraView.swift

CameraView.swift already imports:

import MWDATCore
import SwiftUI
import UIKit
import AVFoundation

The local test added an AVSpeechSynthesizer state object and a speech-test action.

The live camera UI already exposes:

session state

stream state

preview start/stop

photo capture

recording

microphone toggle

start/end session

The sample's LivePreviewView reads the current VideoFrame converted to a UIImage.

31. Separation from the main VS Code codebase

The coding agent should treat the iOS project and the main computer application as separate runtime components.

iOS/Xcode component:
    Meta hardware integration
    Swift
    DAT
    iOS sensors
    iOS networking client
    iOS audio playback

Computer/VS Code component:
    receives project-defined messages from iPhone
    performs main application processing
    returns project-defined actions/data to iPhone

The computer-side code should not import or expect:

MWDATCore
MWDATCamera
Wearables.shared
DeviceSession
VideoFrame

Those are iOS-side concepts.

Likewise, the iOS bridge does not need to contain the computer-side model/reasoning implementation unless explicitly moved there.

32. Official Meta API facts relevant to the coding agent

Current Meta iOS SDK documentation identifies the following:

SDK entry point:
Wearables.shared

Initialization:
Wearables.configure()

Core package:
MWDATCore

Camera package:
MWDATCamera

Device selectors:
AutoDeviceSelector
SpecificDeviceSelector

Session object:
DeviceSession

Camera stream configuration:
StreamConfiguration

Per-frame object:
VideoFrame

Image conversion:
VideoFrame.makeUIImage()

Supported camera FPS:
2, 7, 15, 24, 30

Documented camera resolutions:
360×640
504×896
720×1280

External accessory protocol:
com.meta.ar.wearable

Meta's Device Access Toolkit remains a developer-facing mobile integration layer; it does not create a computer-side Python SDK for the glasses.

33. External references used for these facts

The factual SDK details in this document correspond to the current official Meta sources:

Meta Wearables Device Access Toolkit FAQ

facebook/meta-wearables-dat-ios

Meta iOS DAT Getting Started skill/documentation

Meta CameraAccess sample

Meta iOS DAT repository agent/developer documentation

The exact local Xcode/signing/session details additionally reflect the device configuration that has already been successfully tested in this project.