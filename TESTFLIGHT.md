# TESTFLIGHT.md — VCs run the app on their own glasses (Friday)

Goal: each VC installs a TestFlight build, uses **their own** Ray-Ban Meta glasses, pastes
one URL, and streams to our hosted backend over cellular or any Wi-Fi.

There are two owners. Work top to bottom, because each step has a pass condition. At the
first failure, stop and tell the other person. Don't improvise around it.

- **Rishi** owns the Apple Developer account (through the other company), so he handles
  App Store Connect, the bundle ID, testers and privacy.
- **Aidan** owns the Meta developer account and the Xcode project, so he handles Meta
  registration, the release channel, the build and the upload.

The phone-side code changes are in [ios/INTEGRATION.md](ios/INTEGRATION.md) §9. The ask
and answer test is in [XCODE_ASK.md](XCODE_ASK.md).

## Order of operations (this is the critical path)

```
Wed  Rishi R1 bundle ID + Team ID ──► Aidan A1 Meta registration ──► A3 build ──► A4 upload
     Rishi R2 add Aidan ─────────────────────────────────────────────┘
     Rishi R0 collect VC details ──► R3 app record ──► R5 VCs as internal testers
     Aidan A2 release channel: find out TODAY whether outside Meta accounts are accepted
Thu  PRE-FLIGHT (bottom of this page), over cellular, own glasses, TestFlight build
Fri  VCs: five steps
```

**Internal TestFlight is the only route that certainly lands by Friday.** It has no
Apple review, and a build reaches testers minutes after processing. External testing
adds a beta review of about a day, with real rejection risk (see R6). Run it in parallel
only as a backup.

---

## Rishi: Apple side

### R0. Collect from every VC (today)

| Need | Why |
|---|---|
| The email of their **Apple Account** | They become an App Store Connect user, and TestFlight invites go there |
| The email/identity of the **Meta account** signed into their Meta AI app | Aidan adds it to the Meta release channel (A2) |
| Glasses model (Ray-Ban Meta Gen 1 / Gen 2), iPhone model, iOS version | Deployment target is iOS 17.2; DAT supports the regular Ray-Ban Meta |
| A tester name for their backend slot | The URL is `wss://DOMAIN/t/NAME/ws/glasses?token=…` |

Tell them to update the Meta AI app and the glasses firmware before Friday. SPEC notes
Meta AI app v254+ and firmware v20+.

### R1. Bundle ID (Certificates, Identifiers & Profiles)

1. developer.apple.com → Account → Certificates, Identifiers & Profiles → Identifiers →
   **+** → App IDs → App.
2. Pick an **explicit** bundle ID under the company's reverse-DNS, for example
   `com.<company>.<appname>`. Do not reuse `com.aidanwu.MetaCameraTest`, which belongs
   to Aidan's Personal Team.
3. No extra capabilities are needed. External Accessory and Background Modes are
   Info.plist keys, not App ID capabilities. Don't add Wi-Fi Information / Hotspot.
4. Copy the **Team ID** (Membership details, 10 characters).

Pass: send Aidan the bundle ID and Team ID. **Meta registration (A1) needs both, so this
blocks him.**

### R2. Add Aidan to the team

App Store Connect → Users and Access → **+**:

- Role: **App Manager** (or Developer).
- If they are offered, also tick **Access to Certificates, Identifiers & Profiles** and
  **Access to Cloud Managed Distribution Certificate**. Without the distribution
  certificate access, Xcode can build but cannot sign the upload. Admin also works, but
  it is more access than he needs on another company's account.

Aidan accepts the email invite, then signs into that Apple Account in Xcode → Settings →
Accounts. Pass: the company team appears under his account in Xcode.

### R3. App record

App Store Connect → Apps → **+** → New App:

- Platform: iOS.
- Name: must be unique across the whole App Store, so add a qualifier if it's taken.
- Bundle ID: the one from R1, from the dropdown.
- SKU: any string.
- User Access: Full Access, or Limited with Aidan included.

Pass: the app shows a TestFlight tab.

### R4. Privacy: usage strings and privacy manifest (Rishi writes, Aidan pastes)

**Usage strings (Info.plist).** Make them match what the app actually does. The sample's
defaults talk about recording video, which it does not do here.

| Key | Suggested text |
|---|---|
| `NSBluetoothAlwaysUsageDescription` | Connects to your Ray-Ban Meta glasses. |
| `NSBluetoothPeripheralUsageDescription` | Connects to your Ray-Ban Meta glasses. |
| `NSCameraUsageDescription` | Used only by the built-in test mode that simulates glasses with the phone camera. |
| `NSMicrophoneUsageDescription` | Listens for a few seconds after the glasses ask you a question, to hear your answer. |
| `NSSpeechRecognitionUsageDescription` | Turns your spoken answer into text, on the phone when possible. |
| `NSMotionUsageDescription` | Uses motion to tell stillness from activity. |
| `NSLocationWhenInUseUsageDescription` | Uses your speed (not where you are) to tell walking from sitting. |
| `NSLocalNetworkUsageDescription` | Lets the app reach your glasses and a development server on this network. |
| `ITSAppUsesNonExemptEncryption` | `NO` (Boolean). It uses only Apple's TLS, and this skips the export question on every upload. |

Before shipping, check the location claim: `gps_speed` is the only location-derived
value in the capture packet (`CapturePacketSender.capturePacketJSON`).

**Privacy manifest.** Add a file named `PrivacyInfo.xcprivacy` to the CameraAccess
target (File → New → File → App Privacy):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key>
  <false/>
  <key>NSPrivacyTrackingDomains</key>
  <array/>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>CA92.1</string></array>
    </dict>
  </array>
  <key>NSPrivacyCollectedDataTypes</key>
  <array>
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataTypePhotosorVideos</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><true/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataTypeOtherUserContent</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><true/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataTypeOtherDataTypes</string>
      <key>NSPrivacyCollectedDataTypeLinked</key><true/>
      <key>NSPrivacyCollectedDataTypeTracking</key><false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>
  </array>
</dict>
</plist>
```

- The three collected-data entries mean: frames (PhotosorVideos), answer transcripts
  (OtherUserContent), and motion and speed (OtherDataTypes). They are marked linked
  because each tester's data lands in a slot named after them.
- The **required-reason API** block is the part that can block an upload. After the
  first upload Apple emails any missing declarations (ITMS-91053). Treat that email as
  blocking: add each API category it names, with the closest honest reason code, and
  upload again. Some of those APIs may come from Meta's SDK, since statically linked SDK
  code counts as the app's. That is expected; declare them anyway.

### R5. Add each VC as an INTERNAL tester

Internal testers skip Apple's beta review entirely. The catch is that every internal
tester must be an **App Store Connect user on this team**:

1. **Users and Access → +**, once per VC:
   - Their Apple Account email and name.
   - Role: **Marketing**. Internal testing is open to the Account Holder, Admin, App
     Manager, Developer and Marketing roles, and Marketing is the least-privileged of
     those. Finance, Sales and Customer Support users cannot be internal testers.
   - If app access can be limited, limit it to this one app.
2. The VC gets an email invitation to the company's App Store Connect team and must
   **accept it**, signing in with that Apple Account. Warn them first, because the email
   names the other company.
3. **TestFlight → Internal Testing → + (new group)**, for example "Friday". Add the VCs.
   They are only listed once they have accepted step 2. Turn on automatic distribution,
   or add the build to the group by hand once it has processed.
4. Each VC gets a TestFlight email. A build stays installable for 90 days.

Limits and cleanup:

- There are at most **100 internal testers** per app. Each one is a real team member
  with a role, not a guest.
- **Remove them from Users and Access after the demo.**

### R6. If a VC can't be internal: external testing (backup only)

- **TestFlight → External Testing → new group**, then add testers by email or enable a
  public link. Up to 10,000 testers, and they do not need to be App Store Connect users.
- The first build of each version goes through **Beta App Review**, which usually takes
  about a day but is not guaranteed. Submit Wednesday night at the latest if you want it
  as a backup.
- App Store Connect asks for Test Information: a beta description, a feedback email, and
  a privacy policy URL. A one-page site that repeats the ConsentView text is enough.
- Known rejection risks:
  - **MFi / External Accessory.** The Info.plist declares
    `UISupportedExternalAccessoryProtocols = com.meta.ar.wearable`. Apple can ask for
    the accessory's MFi authorization (the PPID from the accessory maker) before it
    approves an app that declares an accessory protocol. We are not the maker, so only
    Meta can answer that. In the review notes, say the app uses Meta's Wearables Device
    Access Toolkit with Ray-Ban Meta glasses, link a 60-second demo video, and say a
    reviewer needs the glasses to exercise it. If review asks for the PPID, ask Meta
    through the Developer Center support channel. Do not expect an answer by Friday.
  - **Privacy manifest.** A missing required-reason declaration (ITMS-91053), including
    one triggered by the Meta SDK, fails processing or review. R4 covers ours; fix
    anything the email names.
  - **Reviewers can't test it without glasses.** Say so plainly in the notes and include
    the video.

---

## Aidan: Meta side and the build

**Prove distribution first: install the build on a teammate's phone with THEIR Meta
account and glasses before anything else.** Everything below is wasted if a phone that is
not yours, signed into a Meta account that is not yours, will not accept the app.

**Read Meta's developer terms and acceptable-use policy before shipping this to anyone:**
[wearables.developer.meta.com/terms](https://wearables.developer.meta.com/terms) and
[wearables.developer.meta.com/acceptable-use-policy](https://wearables.developer.meta.com/acceptable-use-policy)
(login required). We send glasses camera frames to cloud services (our server, Google
Gemini, OpenAI), so check what those documents say about cloud processing of camera
frames. Do not assume it is approved just because the SDK exposes the camera.

### A1. Register the app in Meta's Wearables Developer Center

The dev build worked through Meta AI **Developer Mode**, with no real Meta app ID. VCs
will not have Developer Mode on. For a VC's phone to accept the app, the build has to
carry the IDs of an app registered in the Developer Center.

1. In the Wearables Developer Center, create (or open) the app/project for this
   integration.
2. Find the **iOS app configuration setting**. It must hold exactly:
   - the **bundle ID from R1**, and
   - the **Apple Team ID from R1**, which is the company's, not your Personal Team
     `28WZ3U2768`.

   If it also asks for a URL scheme or app link, it must match `MWDAT → AppLinkURLScheme`
   in Info.plist (currently `cameraaccess://`) and the app's `CFBundleURLTypes`. Don't
   change the scheme unless the form requires it, because it is tested as-is.
3. Copy the **App ID** and **Client Token** it gives you.
4. Info.plist reads them from build settings as `MWDAT → MetaAppID = $(META_APP_ID)`
   and `ClientToken = $(CLIENT_TOKEN)`. Neither build setting is defined in the project
   today, so they expand to empty. Add both as **User-Defined** build settings on the
   CameraAccess target (Build Settings → + → Add User-Defined Setting). `TeamID` is
   `$(DEVELOPMENT_TEAM)` and updates itself once you switch teams in A3.

Pass: the Developer Center shows the app with the new bundle ID and Team ID, and you
have an App ID and Client Token.

### A2. Release channel and testers (find out today)

The preview documentation says builds go to testers **in your organization**. Before
anything else, find out whether that includes outside Meta accounts.

1. Find the **release channel** (or testing / distribution) setting for the app, and
   create a channel for Friday.
2. Add yourself and Rishi first, by the Meta accounts signed into your Meta AI apps.
   Walk through what a tester has to do to be accepted: an email, a prompt in Meta AI,
   or nothing. Write it down, because it becomes VC instructions.
3. Try to add one VC's Meta account (from R0).
   - **Accepted:** add them all. Done.
   - **Rejected, or only organization members allowed:** check whether the Developer
     Center lets you invite people into the **organization/team** as members. If it
     does, invite each VC's Meta account as a member with the smallest role, then add
     them to the channel.
   - **Neither works:** the fallback is that each VC turns on **Developer Mode in their
     own Meta AI app**, the way you did on yours. Write down the exact taps from your
     phone for the VC card. Then test on Rishi's phone, with Developer Mode on and a
     non-developer Meta account, whether the build with the *registered* App ID
     registers. If only `MetaAppID = 0` works in Developer Mode, upload a second build
     (next build number) with `META_APP_ID = 0` and give that build to the VCs' TestFlight
     group.
   - **Last resort:** VCs wear our glasses on our phone. Decide this by Thursday noon.

Pass: you know which of the four paths Friday uses, and Rishi knows what to tell the VCs.

### A3. Build

In `~/meta-wearables-dat-ios` (never a fresh clone, see XCODE.md §0):

1. Copy in `ios/MacLink.swift` and `ios/CapturePacketSender.swift`, and add
   `ios/ConsentView.swift` to the target. Make the CameraView edits from
   INTEGRATION.md §9: Server URL field, consent sheet, `endpointLabel`, idle timer.
2. Signing & Capabilities:
   - Team: the company team from R2.
   - Bundle Identifier: the one from R1.
   - Automatically manage signing: on.
   - Don't re-add Access Wi-Fi Information or Hotspot, which the app does not need.
3. Info.plist: the R4 strings and `ITSAppUsesNonExemptEncryption = NO`, plus
   `PrivacyInfo.xcprivacy` from R4. Set `CFBundleDisplayName` to the name VCs should see
   on their home screen.
4. Build settings: `META_APP_ID` and `CLIENT_TOKEN` from A1. Set `MARKETING_VERSION` to
   1.0, and increase `CURRENT_PROJECT_VERSION` on **every** upload (1, 2, 3…).
5. Build to your own phone over a cable once. Paste a real tester URL, Connect, agree,
   and stream. Pass: `connected wss://…`, and frames reach the hosted backend.

### A4. Upload

1. Destination: **Any iOS Device (arm64)**. Then Product → **Archive**.
2. In Organizer, select the archive → **Distribute App**. Pick **TestFlight Internal
   Only** if it's offered and we're going internal; otherwise pick **App Store Connect**
   → Upload. Leave the defaults.
3. Wait for the "has completed processing" email, which takes 5–30 min.
   - If validation fails on `UIBackgroundModes → processing` needing
     `BGTaskSchedulerPermittedIdentifiers`, remove `processing`. The app schedules no
     background tasks.
   - If it fails on the app icon, add a 1024×1024 PNG to AppIcon.
   - Any ITMS-91053 email: see R4.
4. Check that the build shows in App Store Connect → TestFlight and is added to the
   internal group.
5. Re-snapshot `ios/xcode-project.patch` (XCODE.md §7). It is the only backup of the
   project edits.

Don't update the Meta SDK Swift package between now and Friday.

---

## What the VC does (send this, with their personal URL, by private message)

The URL carries a password (`token=…`). Send it one-to-one, not in a group chat.

1. **Accept the invites.** First the App Store Connect email from the team: tap Accept
   and sign in with your Apple Account. Then the TestFlight email: on your iPhone,
   install **TestFlight** from the App Store, open the email, tap *View in TestFlight*,
   then **Install**.
2. **Pair your glasses in the Meta AI app** (skip if already paired). Update the Meta AI
   app and the glasses firmware, and keep the glasses on and connected.
3. **Open the app and connect your glasses** when it asks. It hands you to Meta AI to
   approve; tap Allow and it returns. *(Aidan: replace this with the exact button names
   from A2.)*
4. **Tap Setup, paste the server URL we sent you, tap Apply.** Then tap **Connect**,
   read the one-screen notice, and tap **I agree**. You only see it once. The status
   line turns to `connected wss://…`.
5. **Start streaming** and keep the app open with the screen on. If the status ever says
   *invalid access token*, paste the URL again. Anything else fixes itself within about
   10 seconds.

---

## Pre-flight (Thursday): the full VC path, over cellular, own glasses

Run this on Rishi's phone and glasses, from the **TestFlight build** (not an Xcode
build), exactly as a VC would. Record results in FINDINGS.md. Every step must pass
before Friday.

Note on the token: it is **per tester** and lives in the app's UserDefaults inside the
pasted URL, not in the Keychain (a known shortcut for Friday, TODO in `MacLink.swift`).
A leaked token exposes one tester's slot only, and it is revoked by removing that
tester on the backend.

1. [ ] Delete any Xcode-built copy of the app. **Turn Wi-Fi off**, so you are on
   cellular only.
2. [ ] In Meta AI, **turn Developer Mode off**, unless A2 chose the Developer Mode
   path. Use a Meta account that was added to the release channel exactly the way the
   VCs were.
3. [ ] Install from TestFlight. Connect the glasses via the Meta AI handoff. The DAT
   session reaches `started` and the preview shows frames.
4. [ ] Test a wrong token. Paste a URL with a **wrong token**, then Apply, Connect and I
   agree:
   - The consent sheet appears first.
   - The status reads `invalid access token`.
   - After 60 s the backend log shows **one** attempt, not a retry every few seconds.
5. [ ] Paste the **correct** URL and Apply. It connects without asking for consent
   again, and the status reads `connected wss://DOMAIN/t/NAME/ws/glasses`.
6. [ ] Start streaming. On the hosted backend `received` climbs at about 0.67/s and
   `malformed` stays 0. Use the backend's stats endpoint for this tester slot. On weak
   signal the phone's status line may show `busy N` (frames dropped because the last
   send had not finished; expected, never queued) or `stalled N` (a send took more than
   3 s, so the link reconnected). `stalled` climbing on good signal is a bug.
7. [ ] Force an ask over cellular (XCODE_ASK.md Test 1, against the hosted URL):
   - The voice plays in the glasses.
   - The answer lands as `answered`.
   - Normal audio returns afterwards (Test 2).
8. [ ] Turn on airplane mode for 15 s, then off. The app reconnects by itself within
   about 10 s and capture resumes. No taps allowed.
9. [ ] Kill the app and relaunch it. The URL is still there, consent is not asked
   again, and Connect works.
10. [ ] Run a 15-minute soak with the screen on. Note battery drop, phone heat and
    `received` versus phone `sent`. Also lock the phone for 60 s once and note whether
    the stream survives. If it doesn't, the VC card must say "screen on".
11. [ ] Delete and reinstall. The consent sheet appears again on the first Connect.
12. [ ] Remove the tester slot on the backend and confirm its stored frames, logged
    transcripts and database are gone. The consent text promises data is "stored in
    your private test space and deleted when the team removes your test; the team can
    delete it on request", so the deletion has to be real.
13. [ ] Check the TestFlight build is assigned to every VC's group, and every VC has
    accepted the App Store Connect invite (R5 step 2).

If step 3 fails with the VC-style setup, fix that before anything else. That is the
Meta release-channel question (A2), and Apple cannot help with it.
