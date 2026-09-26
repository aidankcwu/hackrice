# Tester setup: Cory's glasses, end to end

Everything that has to happen for a tester (first: Cory) to hear Bryan through his own
Ray-Ban Meta glasses with the Zeroist TestFlight build. Part 1 is the team's, and every
box must be ticked before Cory gets a single message. Part 2 is the card we send him.
Part 3 is what to do when something goes wrong.

Supersedes "What the VC does" in [TESTFLIGHT.md](../TESTFLIGHT.md), which describes the
old CameraAccess app (Setup → Apply → Connect).

What the repo cannot prove, and only this checklist can: the Meta release channel
accepting an outside account, the TestFlight binary carrying the Meta client token, and
the whole Zeroist chain (release-channel registration, hosted wss, voice) on real
glasses. Older builds streamed real frames and played ElevenLabs audio through the
Ray-Bans (hardware_software.md, docs/BUILD_LOG.md); this configuration has not run end
to end yet. Step T10 is that run.

---

## Part 1: the team, before contacting Cory

### T1. Collect from Cory (Rishi)

- [ ] iPhone model and iOS version (Settings → General → About). **Zeroist needs iOS 26
      or newer** (iPhone 11 or newer). If he is on iOS 18, he updates first.
- [ ] The email of the Apple Account signed in on that iPhone (Settings → his name), and
      that two-factor authentication is on.
- [ ] The email of the Meta account signed into his Meta AI app (Meta AI → profile →
      Accounts Center). Not a Managed Meta Account: those cannot join release channels.
- [ ] Glasses model, printed inside the left arm. Ray-Ban Meta Gen 1 or Gen 2 (or
      Oakley Meta HSTN / Vanguard) work. 2021 Ray-Ban Stories do not.
- [ ] He lives in a country where Meta AI glasses are supported.

### T2. Meta Wearables Developer Center (Aidan)

- [ ] App `2802824706754749`, iOS configuration: bundle id `com.zeroist.app`, Team ID
      `3386QS4N44`, URL scheme `bryanglasses://`. Screenshot it.
- [ ] The integration version the Beta channel distributes was created **after** DAT 1.0
      (Meta: older versions do not work with SDK 1.0 builds, which Zeroist is).
- [ ] Beta release channel: add Cory's Meta account email from T1. He must accept the
      emailed invite. Invites he holds are listed at https://wearables.meta.com/invites.
- [ ] If the channel refuses an account outside our organization, follow TESTFLIGHT.md
      A2's fallbacks (invite him into the organization) and update Part 2 to match.

### T3. The TestFlight build (whoever archives)

- [ ] `ios/Brian/Local.xcconfig` exists on the archiving Mac with `CLIENT_TOKEN` for app
      `2802824706754749` (copy from `Local.xcconfig.example`; send the token privately,
      never in git). A Release build now **fails** without it, on purpose.
- [ ] `git pull` on `demo`, then `cd ios/Brian && xcodegen generate`.
- [ ] CFBundleVersion in `ios/Brian/project.yml` is higher than every uploaded build
      (build 1 is taken).
- [ ] Archive (Release, Any iOS Device), upload, wait for processing. Then, without
      printing the value, check the archive:
      `plutil -extract MWDAT.ClientToken raw <archive>/Products/Applications/Zeroist.app/Info.plist | wc -c`
      is well over 1, and `MWDAT.MetaAppID` is `2802824706754749`.
- [ ] App Store Connect → Users and Access → + : Cory's Apple email, role **Marketing**,
      access limited to Zeroist. Text him first that an Apple email naming our company is
      coming and he should accept it. Confirm he no longer shows "Invitation pending".
- [ ] TestFlight → Internal Testing: a group with Cory and the new build. Expire older
      builds so he cannot install one by mistake.

### T4. His backend slot (Aidan, on the Mac mini)

- [ ] The Mac mini stays up for his whole test window: `sudo pmset -a sleep 0
      disablesleep 1`, Docker Desktop starts at login, `tailscale funnel --bg 8080` is
      persistent (`tailscale funnel status`).
- [ ] `deploy/.env`: `DOMAIN=aidan-mini.tail39b2e2.ts.net`, `COMPOSE_FILE` includes
      `docker-compose.yml`, `docker-compose.mac.yml` and `docker-compose.override.yml`,
      the Gemini, OpenAI and ElevenLabs keys have quota, and `ELEVENLABS_VOICE_ID` is a
      real voice the key can use.
- [ ] `git pull`, `docker compose build backend-image`, `docker compose up -d`, from the
      same commit the TestFlight build came from.
- [ ] `./new_tester.sh cory`. Read the whole output. The line under "SEND THIS ONE" is
      the only thing Cory gets.
- [ ] From a phone **on cellular with Tailscale off** (on the tailnet the name resolves
      privately and proves nothing): `https://aidan-mini.tail39b2e2.ts.net/t/cory/healthz`
      → 200, and `/t/cory/api/status` without a token → 401.
- [ ] `/t/cory/api/status` with the token shows speech mode `elevenlabs`.
- [ ] `uv run python tools/vc_smoke.py '<cory wss link>' --seconds 60` while watching
      `docker compose logs -f backend-cory` for `ingest: phone connected`.

### T5. The dry run (Rishi or Aidan, before anything goes to Cory)

On a teammate's iPhone (iOS 26+), with the **TestFlight build**, a Meta account that
joined through the release channel exactly as Cory's will, Meta AI Developer Mode
**off**, Wi-Fi **off**. Walk Part 2 word for word, against a throwaway tester slot.
Screenshot every Meta AI screen; they go into Part 2.

- [ ] Questionnaire, then Connect opens.
- [ ] Paste link → Invite link row green.
- [ ] Register → Meta AI approval → back in Zeroist, Glasses row green.
- [ ] Start watching → consent → Meta AI camera permission → frames flow (Stream row).
- [ ] Test voice: heard in the glasses. Then with the **Ring/Silent switch on Silent**:
      still heard. Then with the **phone locked for 60 s**: note whether audio and frames
      survive.
- [ ] A question from Bryan (docs/XCODE_ASK.md Test 1): answer out loud, it lands.
- [ ] Take the glasses off for 30 s: the app says the glasses stopped; put them on, Start
      watching, frames resume.
- [ ] Airplane mode 15 s, then off: frames resume without taps.
- [ ] Kill and reopen the app: no questionnaire, link still green.

Record every result in FINDINGS.md. **Do not send Cory anything until every box passes.**

---

## Part 2: Cory's card (send one-to-one; the link is a password)

Send the link in its **own message**, nothing else in that bubble.

**Before you start** (the day before)
1. Charge the glasses: in the case, case plugged in, until the light is green. Charge
   your phone.
2. Update your iPhone to iOS 26 if it isn't already (Settings → General → Software
   Update).
3. Update the **Meta AI** app from the App Store, sign in with the Meta account you gave
   us, and let it update the glasses (glasses in the case, next to the phone, until
   Meta AI says they are up to date).
4. Accept three invites, on your iPhone:
   - Apple's email inviting you to our App Store Connect team: tap Accept and sign in with
     your Apple Account. Nothing else to do on that site.
   - Meta's email inviting you to Zeroist's release channel: accept it. Then in Meta AI,
     under your glasses' settings, pick Zeroist's release channel. *(Screens from T5.)*
     You do not need Developer Mode.
   - TestFlight's email: install **TestFlight** from the App Store, open the email, tap
     *View in TestFlight*, then **Install**. The app is called **Zeroist**; Bryan is
     the voice.

**Setting up** (about 10 minutes, glasses in hand)
5. Bluetooth on in Settings (not just Control Center). Take the glasses out of the case
   and unfold them. Meta AI should show them connected.
6. Open Zeroist. Allow Bluetooth if asked. Answer the questions (about a minute).
7. The **Connect** screen opens. Stay on it until all three rows are green. If you close
   it, tap the status pill at the top left to get it back.
8. **Invite link:** copy the link we sent (long-press the message → Copy). In Zeroist tap
   *Use the link on your clipboard* (allow paste), or paste it into the field. The row
   turns green.
9. **Glasses:** tap **Register**. Meta AI opens: approve Zeroist *(screen from T5)*. You
   come back to Zeroist and the row turns green.
10. Open **Permissions** on the same screen and allow Microphone, Speech Recognition and
    Notifications.

**Every session**
11. For your first sessions: Settings → Display & Brightness → Auto-Lock → **Never**, Low
    Power Mode off, AirPods and car audio disconnected. Keep Zeroist open on screen.
12. Glasses on your face, arms unfolded. Tap **Start watching** once and wait.
    - First time: read the notice and tap **I agree**.
    - First time: Meta AI asks to let Zeroist use the glasses camera: **Allow**, then come
      back to Zeroist.
13. Tap **Test voice**. You should hear Bryan in the glasses. Set the volume by swiping
    along the right arm of the glasses, or with the phone's volume buttons.
14. Use your day. Bryan speaks when something is worth saying, a few seconds after it
    happens, and not often. If he asks you something, answer out loud right after he
    finishes.
15. Don't cover the small camera light on the glasses, press the capture button, or say
    "Hey Meta" while Zeroist is watching.
16. Tap **Stop** when you're done. Next time: open Zeroist, glasses on, Start watching.

---

## Part 3: when something goes wrong

| What Cory sees | What to do |
|---|---|
| Invite link red: "does not look right" / "Nothing answers" | Tap **Change**, paste only the wss:// link from our message. |
| Invite link red: server unreachable | Tell us; the server is ours to fix. Tap Try again later. |
| Invite link red: access token not accepted | Ask us for a fresh link, tap **Change**, paste it. |
| Glasses row red after Register | Open Meta AI: glasses connected? Release-channel invite accepted and selected? Then Register again. |
| "This build of Zeroist is missing its Meta setup" | Our mistake in the build. Tell us; we send a new one. |
| "Your glasses did not respond" | Glasses out of the case, unfolded, on your face; in Meta AI they show connected. Still stuck: turn the glasses off and on (switch inside the left arm), then Start watching. |
| "Zeroist needs camera access" | In Meta AI, allow Zeroist to use the glasses camera, come back, Start watching. |
| "The glasses stopped sending video" | You took them off, folded them or paused them. Put them on, Start watching. |
| Battery / too warm | Charge them in the case / let them cool, then Start watching. |
| No voice | Test voice. Check the output in Control Center (AirPlay icon → Ray-Ban Meta), volume up, Speak through the glasses on in Settings. |
| After a phone call Bryan stays quiet | Stop, then Start watching. |

After the test: `./remove_tester.sh cory` (deletes his data, as the consent notice
promises), remove him from App Store Connect and from the release channel.
