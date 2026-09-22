# README_FIRST — Brian iOS build kit for Claude Code

Unzip this at the root of the `hackrice` repo. It adds a plan, five subagents, the design
skills, settings, and an XcodeGen starter. Claude Code then builds the app job by job.
You sign, run on the phone, and say "go" at each gate.

## 1. Before anything (once, ~20 minutes)

| Need | Why | How |
|---|---|---|
| Mac with Xcode 26 or 27, Apple-silicon | builds iOS 26 apps | App Store; then `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` |
| XcodeGen | writes the Xcode project from `ios/Brian/project.yml` | `brew install xcodegen` |
| `uv`, Node 20+, `gh` | backend tests, dashboard, the final PR | `brew install uv node gh` |
| Claude Code, Max plan | the orchestrator runs on Opus | `npm i -g @anthropic-ai/claude-code` |
| iPhone on iOS 26+, cable, Ray-Ban Meta glasses, Meta AI app with Developer Mode on | the real thing | same phone Aidan streamed from |
| Apple Developer Program ($99/yr) | Family Controls (Job 4) will not provision on a free Personal Team, confirmed. TestFlight to Bryan's testers needs it too, and builds stop expiring every 7 days | developer.apple.com, buy it today so it clears before Job 3 |

## 2. Install (5 commands)

```bash
cd ~/hackrice                      # your clone
unzip -o ~/Downloads/brian-claude-code.zip -d .
bash setup.sh                      # makes branch brian-ios from origin/reactive-glasses, commits the kit
claude --model opus
```
Then paste into Claude Code:
```
Read PLAN.md. You are the orchestrator. Follow section 0. Start Job 0.
```
When Claude Code asks to approve the project MCP servers (`meta-wearables-docs`,
`apple-docs`), say yes. Both are documentation lookups.

## 3. The loop (same every job)

1. Claude Code works through the job's tasks with subagents. You'll see short reports,
   not code. Leave it alone.
2. It stops with "Job N done. Run the STOP gate." Do the gate (it is written in
   `PLAN.md` under that job: run on device, hold up a cup, etc.). Takes 15–60 min.
3. Type `/clear`, then paste: `Read PLAN.md. You are the orchestrator. Follow section 0. Start Job N+1.`

Five jobs. Job 1 is the big one (3–5 hours of Claude Code time). The whole thing is
about two working days of Claude Code and one day of your time, mostly waiting and
testing on the phone.

## 4. What each job leaves you with

| Job | You get |
|---|---|
| 0 | `docs/STATE.md` (the map), fixtures captured from the real backend |
| 1 | `ios/Brian/Brian.xcodeproj`: Setup, Today (status, Start watching, hours, ledger), Settings, whispers + notifications. Streams to the Mac like the old app did. Screenshots graded ≥ 16/20 |
| 2 | Protocol tab; vial in hand inside a window = dose logged with a thumbnail, no tap; missed window = one whisper; dashboard 14-day grid + CSV for the clinician |
| 3 | Apple Health (sleep, HRV, steps) feeding the score. No Fitbit needed |
| 4 | The system acts: calendar block when daylight is short, app shield at wind-down. Ledger rows read "acted" |
| 5 | Everything verified, `docs/DEMO_BRYAN.md`, a PR into `reactive-glasses` |

## 5. If it stops

| It says | You do |
|---|---|
| `BLOCKED` twice on one task | read the last 10 lines of `.claude/logs/<task>.log`; usually a missing tool or a signing issue. Fix, then paste `Retry task <id>.` |
| signing fails on Job 3 or 4 | that is the paid Apple account. Once it's active, in Xcode set the Team again, then `Retry task <id>.` |
| "Device unavailable" / CoreBluetooth API MISUSE | power-cycle the glasses, foreground Meta AI, try again. Not a build problem (ios/README.md) |
| the WebSocket never connects | phone and Mac on the same Wi‑Fi, backend on port 8010, address typed as `10.0.0.5:8010` not `localhost` |
| context feels slow or dumb | you skipped a `/clear`. Do it now and re-paste the Start line |

## 6. What's in the box

```
PLAN.md                      the plan the orchestrator follows (jobs, tasks, acceptance, STOP gates, effort)
CLAUDE.md                    replaces the old one (the old one said "I am Person A" and would refuse backend work)
docs/IOS_SPEC.md             every screen, state, and line of copy
.claude/settings.json        effort high, pre-approved build/test commands, push and rm ask first
.claude/agents/              scout · ios-builder · backend-builder · verifier · design-critic
.claude/skills/brian-ios-design/   the look: palette, type, banned list, the 20-point rubric.
                             Inherits the repo's existing brian-ui skill and its voice.md
.claude/skills/              vendored: swiftui-design-skill (anti-slop), swiftui-expert-skill,
                             swiftui-liquid-glass, healthkit, eventkit, background-processing,
                             ux-writing, sf-symbols, and Meta's 8 DAT skills
.mcp.json                    Meta wearables docs + Apple docs, lookup only
ios/Brian/project.yml        XcodeGen starter with every Info.plist key the glasses and permissions need
setup.sh                     branch + folders + tool check + first commit
```

Licences for the vendored skills sit next to each SKILL.md (MIT, dpearson2699's notice,
Meta's developer terms).
