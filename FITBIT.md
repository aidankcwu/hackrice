# FITBIT.md — connect the Fitbit through the Google Health API (whoever wears it)

The old Fitbit Web API and dev.fitbit.com are shut down (September 2026). Fitbit
data now comes through the **Google Health API** with **Google OAuth**. The
backend has a poller for it (intraday heart rate at 1-second resolution, HRV,
SpO2, resting HR, breathing rate, skin temperature, sleep stages, steps,
workouts, every 5 minutes). What is missing is a Google Cloud OAuth client and
one approval click. Budget: 20 min. No app review is needed: a client in
"Testing" status reads data for up to 100 listed test users.

You need: the Google account the Fitbit is linked to (the one the Fitbit app
signs in with), and access to the laptop running the backend (Rishi's Mac,
port 8010) for step 4.

## 1. Create the OAuth client in Google Cloud (~10 min, any browser)

Sign in to https://console.cloud.google.com with the Fitbit's Google account.

1. **Project.** Create a project (any name, e.g. `hackrice-glasses`).
2. **Enable the API.** Open
   https://console.developers.google.com/apis/library/health.googleapis.com
   and click **Enable** ("Google Health API").
3. **Consent screen / audience.** Go to https://console.developers.google.com/auth/audience
   (APIs & Services → OAuth consent / Google Auth Platform):
   - User type: **External**. Publishing status: leave as **Testing**.
   - **Test users → Add users:** the Fitbit's Google account email. This is
     the step that lets it read your data without the verification review.
   - App name / support email / developer email: anything valid.
4. **Scopes.** Go to https://console.developers.google.com/auth/scopes → Add
   or remove scopes → paste these three, one per line, and add them:
   ```
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   https://www.googleapis.com/auth/googlehealth.sleep.readonly
   https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   ```
   They will be listed as restricted. That is fine in Testing status.
5. **Credentials.** APIs & Services → Credentials → **Create credentials →
   OAuth client ID** → Application type **Web application**:
   - Authorized redirect URI, exactly:
     `http://localhost:8010/api/wearables/google-health/callback`
   - Create. Copy the **Client ID** and **Client secret**.

## 2. Put the credentials on the laptop (whoever is at the Mac)

Never paste them into chat, Slack, or a commit. On the laptop:

```bash
printf 'GOOGLE_HEALTH_CLIENT_ID=...\nGOOGLE_HEALTH_CLIENT_SECRET=...\n' >> "/Users/rishi/Github Coding /hackrice26/hackrice/backend/.env"
```

Check without printing the secret (should print `2`):

```bash
grep -c '^GOOGLE_HEALTH_CLIENT_' "/Users/rishi/Github Coding /hackrice26/hackrice/backend/.env"
```

## 3. Restart the backend (Rishi / Claude)

The backend reads `.env` at startup. After the restart the phone taps
"Connect to Mac" again.

## 4. Authorize (you, in a browser ON THE LAPTOP)

The redirect URI is `localhost`, so this must be a browser on the machine
running the backend.

1. Open `http://localhost:8010/api/wearables/google-health/authorize`
2. Google's sign-in appears. Use the Fitbit's Google account. Because the app
   is unverified you will see **"Google hasn't verified this app"** → click
   **Continue** (it is your own app and you are a listed test user). Tick all
   three permissions → Continue.
3. You land on "Google Health connected — you can close this tab". The token
   is stored in `backend/data/google_health_token.json` (gitignored) and
   polling starts immediately.
4. Open the Fitbit app on your phone once so the tracker syncs now.

## 5. Verify (anyone)

```bash
curl -s localhost:8010/api/wearables/google-health/status
```
Expect `"configured": true, "connected": true, "polling": true`, a
`last_sync_t`, and `scopes` listing all three. `last_error` names the problem
if there is one.

```bash
curl -s localhost:8010/api/wearables/status
```
Expect `"live_connected": true` and `fitbit` rows with `"origin": "live"`.
Dashboard: heart-rate strip header `live: fitbit`, `LIVE` pills on the tiles,
7-day panel rows carrying `fitbit`.

## What you get, and what you don't

- Heart rate at 1-second resolution, HRV, SpO2, resting HR, respiratory rate,
  skin temperature, sleep stages, steps, workouts. Real, labelled `fitbit`.
- Data reaches Google only when the tracker syncs to the Fitbit app, roughly
  every 15 minutes or when you open the app. For the heart-rate demo moment,
  open the app right after.
- Anything the API does not supply stays seeded and labelled as such.

## If something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `configured: false` / authorize 503 | env lines missing or no restart | steps 2–3 |
| Google: `redirect_uri_mismatch` | redirect URI typo in the client | step 1.5, exact string |
| Google: `access_denied` / "app not verified" with no Continue | your email is not a test user | step 1.3 |
| `403` with `PERMISSION_DENIED` in `last_error` | Google Health API not enabled on the project, or scope missing | steps 1.2, 1.4 |
| `connected: true`, no data | tracker has not synced | open the Fitbit app |
