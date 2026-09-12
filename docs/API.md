# Backend API and seam contract

Everything the dashboard reads, and the two places Person A's code touches
Person B's. Paths are relative to the FastAPI service (default `:8000`).

## The seam (SPEC §13.3)

1. **Ticks in.** A's T0 publishes one `pipeline.models.Tick` per second to the
   in-process `pipeline.bus.TickBus` (`bus.publish(tick)`). Non-blocking; never
   queues. B's gate, episode builder, and tick store are subscribers.
2. **Frames.** A owns the ring buffer and `GET /frames?refs=f_1,f_2` (JSON
   `{ref: base64_jpeg}`, expired refs omitted, all-expired → 410). B's reasoner
   calls `pipeline.frames.FrameStore.get(refs)` in-process; the HTTP route is
   the same store exposed for debugging.
3. **Speech out.** B calls `speak(text: str, urgency: Literal["low","normal","high"])`
   after its rate limiter. A implements it (ElevenLabs → socket → phone →
   glasses). B ships a stub that logs. Wire A's implementation via
   `pipeline.actions.speech.set_speak_fn(fn)` at startup. **`speak()` is
   fire-and-forget**: it must return immediately (schedule its own task) and
   never raise into B's action handler.

Agreed details (from plan review):

- **Frame encoding.** `FrameStore.get(refs)` returns `dict[ref, jpeg_bytes]`;
  the HTTP route base64-encodes. Partial expiry returns the survivors; only
  all-expired is a 410. B copies the four selected frames out of the store
  **at escalation admission**, before inference, into a durable
  `escalated_frames` table — that copy is the only way a frame outlives 90 s
  (SPEC §2.5).
- **One clock.** Every timing decision downstream (cooldowns, watches, frame
  TTL, episode boundaries) uses `tick.t`, never wall-clock. A must stamp `t`
  from the capture packet. This is what lets `--source sim --speed 10` run
  the whole pipeline faster without desyncing anything.
- **One FastAPI app.** `pipeline.api.app:create_app()` is the single server.
  A adds a router (WebSocket ingest, `/frames`) to it; B's `/frames` is
  simulation-only and is replaced, not duplicated, when A's router mounts.
- **TickBus is B's choice, not the contract.** The contract is the Tick
  object. If A prefers to hand B a callback instead, `bus.publish` is that
  callback.

## Dashboard REST

| Method / path | Returns |
|---|---|
| `GET /api/status` | `{demo_mode, source, uptime_s, tick_count, ai_coverage, t1_busy, dropped_escalations, last_tick_t}` |
| `GET /api/ticks/recent?n=60` | last n ticks, no pixels |
| `GET /api/episodes?day=YYYY-MM-DD` | episodes (default today), open ones included |
| `GET /api/decisions?limit=50` | decision feed, newest first. **Includes silent decisions** — every escalation produces one row (SPEC §6) |
| `GET /api/insights?limit=50` | `log_insight` rows |
| `GET /api/scores?period=daily\|weekly` | per-metric scores with `source: live\|seeded`, `grade`, `target`, `value`, `score` (0–1) |
| `GET /api/pending_checks` | open `watch` rows |
| `GET /api/summary/today` | annotate lines accumulated today (part 4 of the T1 envelope) |
| `GET /api/seeded?days=7` | seeded integration rows (for the "7-day" panel) |
| `GET /api/biometrics?metric=heart_rate&from=&to=` | `{metric, source, points: [[t, value], ...]}` — seeded wearable series on the tick clock (SPEC §14.2); defaults to the last hour |
| `GET /api/events` | SSE stream (deferred — dashboard polls at 1 s for the demo) |
| `GET /frames?refs=` | see seam §2 |

Decision row shape:

```json
{
  "id": "d_0007", "t": 1757700842.0,
  "trigger": "food_in_frame", "trigger_tick_id": "t_00001742",
  "episode_id": "e_0003",
  "interpretation": "Mixed lunch with two colleagues, restaurant.",
  "confidence": 0.81,
  "actions": [
    {"type": "annotate", "line": "12:31 lunch, mixed plate, with people"},
    {"type": "log_insight", "category": "diet", "text": "..."},
    {"type": "watch", "after_s": 900, "reason": "check if still seated"},
    {"type": "speak", "text": "...", "urgency": "low"}
  ],
  "spoke": false,
  "dropped": false, "drop_reason": null,
  "latency_ms": 2140, "model": "gpt-5.4-mini"
}
```

`spoke` is what actually reached `speak()` after the rate limiter; a `speak`
action can be present with `spoke: false`.

## Feed line format (dashboard)

`12:31 · food_in_frame · mixed lunch w/ people · annotate, log_insight · silent`

One line per decision, silent ones dimmed, spoken ones highlighted.
