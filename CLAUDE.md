# CLAUDE.md

## What this is

Brian: a healthspan system on Ray-Ban Meta glasses. The glasses see what a wrist wearable
cannot (what you ate, whether you got outside, who you talked to, what is in your hand).
The Mac decides. The phone streams, plays, shows, and acts. The thesis is Bryan Johnson's:
remove decisions from the wearer, don't add data.

Architecture in one line: glasses → iPhone (Swift, Meta DAT) → WebSocket over LAN → Mac
(Python, all logic) → audio back to the phone → glasses speakers.

## How work happens here

- `PLAN.md` is the plan. An Opus orchestrator dispatches the subagents in
  `.claude/agents/`; builders never commit, the orchestrator does. One job per session.
- `docs/STATE.md` is the map of the code with `path:line` refs (written in Job 0).
  Read it before touching anything. `docs/IOS_SPEC.md` is the phone app spec.
- `.claude/skills/brian-ios-design` is the phone app's look and rubric. The phone app
  (`phone/` on the web, `ios/Brian` native) inherits nothing from `dashboard/` or
  `design-system/`; those belong to the judges' dashboard and are not references.
- Branch `brian-ios`. Commits are `task <id>: <one line>`. Never push, never force.

## Commands (verify against docs/STATE.md "Commands" once Job 0 has run)

- Backend, no API keys: see STATE.md §1. Demo form: `cd backend && uv run python -m pipeline.main --source sim --speed 3 --port 8010`
- Backend tests: `cd backend && uv run pytest -q`. Root tests: `uv run pytest -q`.
- Dashboard: `cd dashboard && npm run dev` (needs `NEXT_PUBLIC_API_BASE=http://localhost:8010` in `.env.local`).
- iOS: `cd ios/Brian && xcodegen generate`, then the xcodebuild lines in STATE.md "iOS build".
- Logs from any long command go to `.claude/logs/<task>.log`, never into the conversation.

## Invariants (do not violate)

1. T0 never blocks; drop, never queue; the VLM call is fired, not awaited.
2. Frames live only in the Mac's 90 s RAM ring buffer, except as saved decision evidence.
   The phone never writes a frame or thumbnail to disk.
3. §9 vision fields live in one place: `src/longevity/ai_fields.py`. Nothing else names one.
4. The wire contract in `src/longevity/wire.py` is added to, never changed.
5. The phone decides nothing about health. It executes an `act` the backend asked for.

## Pointers

- `SPEC.md` (§2, §3 authoritative; §7–§9 numbers are not settled), `hardware_software.md`
  (hardware-validated, trust it), `FINDINGS.md` (measured facts), `docs/API.md` (routes),
  `docs/CONVERSATION_DESIGN.md`, `docs/WEARABLES.md`, `ios/README.md` (the old gotchas,
  still true: physical iPhone only, DAT availability is transient, ignore `OSStatus -50`,
  never open the glasses microphone, free provisioning expires after 7 days).
