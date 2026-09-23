# Patches owned by the capture supervisor

Do not apply this file from the backend/deploy subtask. `src/longevity/server/ingest.py`
is owned by the capture workstream. The exact proposed patch adds a bounded phone-send
deadline and suppresses answer transcript text in hosted logs.

```diff
diff --git a/src/longevity/server/ingest.py b/src/longevity/server/ingest.py
--- a/src/longevity/server/ingest.py
+++ b/src/longevity/server/ingest.py
@@ -47,6 +47,7 @@
 # Person B evaluates — for a value that only ever matters to ~100 ms.
 MAX_CLOCK_SKEW_S = 60.0
 INGEST_IDLE_TIMEOUT_S = float(os.environ.get("INGEST_IDLE_TIMEOUT_S", "30.0"))
+PHONE_SEND_TIMEOUT_S = float(os.environ.get("PHONE_SEND_TIMEOUT_S", "5.0"))
 
 
 @dataclass(frozen=True, slots=True)
@@ -248,7 +249,9 @@ class GlassesLink:
         rather than left to be picked again for the next half of the exchange.
         """
         try:
-            await websocket.send_text(message)
+            await asyncio.wait_for(
+                websocket.send_text(message), timeout=PHONE_SEND_TIMEOUT_S
+            )
         except Exception as exc:  # noqa: BLE001
             log.warning("ingest: send to phone failed: %s: %s", type(exc).__name__, exc)
             self.drop_client(websocket)
@@ -269,7 +272,9 @@ class GlassesLink:
         sent = 0
         for ws in list(self.clients):
             try:
-                await ws.send_text(message)
+                await asyncio.wait_for(
+                    ws.send_text(message), timeout=PHONE_SEND_TIMEOUT_S
+                )
                 sent += 1
             except Exception as exc:  # noqa: BLE001
                 log.warning("ingest: send to phone failed: %s: %s", type(exc).__name__, exc)
@@ -592,10 +597,16 @@ async def _handle_answer(
 
     link.n_answers += 1
     recv_t = time.time()
-    log.info(
-        "ingest: answer to %s: heard=%s %r (phone t=%s)",
-        question_id, heard, text[:80], msg.get("t"),
-    )
+    if os.environ.get("HOSTED", "").strip().lower() in {"1", "true", "yes", "on"}:
+        log.info(
+            "ingest: answer to %s: heard=%s text_len=%d (phone t=%s)",
+            question_id, heard, len(text), msg.get("t"),
+        )
+    else:
+        log.info(
+            "ingest: answer to %s: heard=%s %r (phone t=%s)",
+            question_id, heard, text[:80], msg.get("t"),
+        )
     log.debug("ingest: answer %s phone clock %s vs mac %.3f", question_id, msg.get("t"), recv_t)
```

Tests to route with the patch: use a socket whose `send_text` never completes and
assert both methods return/drop it within the configured deadline; under `HOSTED=1`,
capture the answer log and assert it contains `text_len=N` but not the transcript.
