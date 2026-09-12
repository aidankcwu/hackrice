# FITBIT.md — connect the Fitbit (whoever is wearing it)

The backend already has the Fitbit integration (OAuth with PKCE, token
refresh, a poller that pulls intraday heart rate, HRV, SpO2, breathing rate,
skin temperature, sleep stages, steps, and workouts every 5 minutes). What is
missing is your account's credentials and one approval click. Budget: 15 min.

You need: the Google account the Fitbit is registered to, and access to the
laptop running the backend (Rishi's Mac, port 8010) for step 4.

## 1. Register a Fitbit developer app (~5 min, any browser)

1. Go to https://dev.fitbit.com/apps/new and sign in with the account the
   Fitbit syncs to.
2. Fill in the form:
   - Application Name / Description / Organization / Website: anything.
   - **OAuth 2.0 Application Type: Personal** — this is the setting that
     allows intraday data for your own account with no approval process.
     "Client" or "Server" will not return the 1-second heart-rate series.
   - **Redirect URL, exactly:** `http://localhost:8010/api/wearables/fitbit/callback`
   - Default Access Type: Read Only.
3. Agree to the terms and save. Copy the **OAuth 2.0 Client ID** and the
   **Client Secret** from the app page.

## 2. Put the credentials on the laptop (whoever is at the Mac)

Never paste them into chat, Slack, or a commit. Run this in a terminal on the
laptop, replacing both `...`:

```bash
printf 'FITBIT_CLIENT_ID=...\nFITBIT_CLIENT_SECRET=...\n' >> "/Users/rishi/Github Coding /hackrice26/hackrice/backend/.env"
```

`backend/.env` is gitignored. Confirm the two lines are there without
printing the secret:

```bash
grep -c '^FITBIT_CLIENT_' "/Users/rishi/Github Coding /hackrice26/hackrice/backend/.env"
```

Should print `2`.

## 3. Restart the backend (Rishi / Claude)

The backend reads `.env` at startup. Restart it; the phone will need to tap
"Connect to Mac" again afterwards.

```bash
cd "/Users/rishi/Github Coding /hackrice26/hackrice/backend" && uv run python -m pipeline.main --source glasses --vlm gemini --reasoner openai --port 8010
```

## 4. Authorize (you, in a browser ON THE LAPTOP)

The redirect URL says `localhost`, so this step must happen in a browser on
the machine running the backend.

1. Open `http://localhost:8010/api/wearables/fitbit/authorize`
2. You are bounced to Fitbit. Sign in as the Fitbit account, tick all the
   permissions (heart rate, sleep, oxygen, respiratory rate, temperature,
   cardio fitness, activity, profile), Allow.
3. You land on a plain "Fitbit connected — you can close this tab" page.
   The backend has stored the token (in `backend/data/fitbit_token.json`,
   gitignored) and started polling. The first sync runs immediately.
4. Open the Fitbit app on your phone once. That forces the tracker to sync
   so today's data reaches Fitbit's servers now rather than in 15 minutes.

## 5. Verify (anyone)

```bash
curl -s localhost:8010/api/wearables/fitbit/status
```
Expect `"configured": true, "connected": true, "polling": true` and a
`last_sync_t`. If `last_error` is set, read it: `paid_plan_required` or
`insufficient_scope` means the app type or permissions in step 1 are wrong.

```bash
curl -s localhost:8010/api/wearables/status
```
Expect `"live_connected": true` and `fitbit` rows with `"origin": "live"`.

On the dashboard (`http://localhost:3000`): the heart-rate strip header reads
`live: fitbit`, the tiles show a `LIVE` pill instead of `SEEDED`, and the 7-day
panel's resting HR, sleep, and HRV rows carry `fitbit` as the device.

## What you get, and what you don't

- Intraday heart rate at 1-second resolution, HRV every 5 min during sleep,
  SpO2 per minute, breathing rate, skin temperature, sleep stages, steps,
  workouts, VO2 max. All real, all labelled `fitbit`.
- Data reaches the API only when the tracker syncs to the phone app, roughly
  every 15 minutes or when you open the app. "Live" means minutes of lag.
  For the heart-rate-anomaly demo, open the app right after the moment you
  want captured.
- Rate limit is 150 requests/hour; the poller uses at most 8 per 5-minute
  cycle, so there is headroom for the demo.
- Anything Fitbit does not supply stays seeded and labelled as such.

## If something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `configured: false` | env lines missing or backend not restarted | steps 2–3 |
| Authorize page 503s | same | steps 2–3 |
| Fitbit page says invalid redirect | redirect URL typo in the app registration | step 1, exact string above |
| `last_error` mentions scope | not all permissions ticked at Allow | re-open the authorize URL |
| `paid_plan_required` on intraday | app type is not Personal | step 1, recreate the app |
| `connected: true` but no data | tracker has not synced | open the Fitbit app |
