#!/usr/bin/env python3
"""Remote preflight for one hosted glasses tester.

    uv run python tools/vc_smoke.py \
        'wss://example.test/t/alice/ws/glasses?token=secret' --seconds 60

The script is intentionally a client only: it exercises the same wire messages as the
phone and observes only the public hosted API.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

from longevity import wire  # noqa: E402


@dataclass(frozen=True)
class Target:
    ws_url: str
    api_base: str
    prefix: str
    token: str


@dataclass(frozen=True)
class StreamVerdict:
    passed: bool
    ticks: int
    coverage: float
    decision_produced: bool
    reason: str


def parse_target(value: str) -> Target:
    """Validate the hosted socket URL and derive its HTTPS API root."""
    parsed = urlsplit(value)
    if parsed.scheme != "wss" or not parsed.netloc:
        raise ValueError("URL must be an absolute wss:// URL")
    marker = "/ws/glasses"
    if not parsed.path.endswith(marker):
        raise ValueError("URL path must end in /ws/glasses")
    values = parse_qs(parsed.query, keep_blank_values=True)
    tokens = values.get("token", [])
    if len(tokens) != 1 or not tokens[0]:
        raise ValueError("URL must contain exactly one non-empty ?token=TOKEN")
    prefix = parsed.path[: -len(marker)].rstrip("/")
    api_base = urlunsplit(("https", parsed.netloc, prefix, "", ""))
    return Target(value, api_base, prefix, tokens[0])


def auth_headers(target: Target) -> dict[str, str]:
    return {"X-Access-Token": target.token}


def stream_verdict(
    before: dict[str, Any], after: dict[str, Any], decisions_before: int, decisions_after: int
) -> StreamVerdict:
    ticks = max(0, int(after.get("tick_count", 0)) - int(before.get("tick_count", 0)))
    coverage = float(after.get("ai_coverage", 0.0) or 0.0)
    decision = decisions_after > decisions_before
    reasons: list[str] = []
    if ticks == 0:
        reasons.append("no new ticks")
    if coverage < 0.5:
        reasons.append(f"AI coverage {coverage:.2f} is below 0.50")
    # A decision is reported, but is not a failure condition in the requested contract.
    return StreamVerdict(not reasons, ticks, coverage, decision, "; ".join(reasons))


def wrong_token_url(target: Target) -> str:
    parsed = urlsplit(target.ws_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["token"] = [target.token + "-wrong"]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))


def generated_jpeg(seq: int, px: int = 512) -> bytes:
    """Make a valid, changing JPEG without requiring test-only packages."""
    image = Image.new("RGB", (px, px), ((seq * 17) % 255, 48, 92))
    draw = ImageDraw.Draw(image)
    x = (seq * 31) % (px - 110)
    draw.rectangle((x, 80, x + 110, 300), fill=(238, (seq * 29) % 255, 50))
    draw.text((24, 24), f"hosted smoke frame {seq}", fill="white")
    output = io.BytesIO()
    image.save(output, "JPEG", quality=70)
    return output.getvalue()


def load_frames(directory: Path | None) -> list[bytes]:
    if directory is None:
        return []
    if not directory.is_dir():
        raise ValueError(f"frames directory does not exist: {directory}")
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if not paths:
        raise ValueError(f"no .jpg or .jpeg files in {directory}")
    frames = [p.read_bytes() for p in paths]
    for path, data in zip(paths, frames):
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError(f"invalid JPEG {path}: {exc}") from exc
    return frames


def rows(payload: Any, key: str | None = None) -> list[dict[str, Any]]:
    if key and isinstance(payload, dict):
        payload = payload.get(key, [])
    return payload if isinstance(payload, list) else []


async def get_json(client: httpx.AsyncClient, path: str) -> Any:
    # A leading slash would discard the reverse-proxy prefix from base_url.
    response = await client.get(path.lstrip("/"))
    response.raise_for_status()
    return response.json()


async def check_tls_auth(target: Target) -> tuple[bool, str]:
    try:
        async with connect(target.ws_url, open_timeout=10, max_size=8 * 1024 * 1024) as ws:
            await ws.send(wire.hello_message(device="vc_smoke", caps=["ask"]))
            await asyncio.sleep(0.15)
            if ws.close_code is not None:
                return False, f"authorized hello was closed with code {ws.close_code}"
    except Exception as exc:
        return False, f"authorized TLS connection failed: {type(exc).__name__}: {exc}"

    try:
        async with connect(wrong_token_url(target), open_timeout=10) as ws:
            try:
                await asyncio.wait_for(ws.recv(), 3)
            except ConnectionClosed as exc:
                if exc.code == 4401:
                    return True, "authorized hello accepted; wrong token closed 4401"
                return False, f"wrong token closed with {exc.code}, expected 4401"
            except asyncio.TimeoutError:
                return False, "server accepted wrong token (socket remained open)"
            return False, "server accepted wrong token"
    except ConnectionClosed as exc:
        return (exc.code == 4401, f"wrong token closed with {exc.code}, expected 4401")
    except Exception as exc:
        return False, f"wrong-token check failed: {type(exc).__name__}: {exc}"


async def stream(
    target: Target, client: httpx.AsyncClient, seconds: float, frame_files: list[bytes]
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "asks": [], "answered_ids": set(), "audio_bytes": 0, "speak": 0,
        "first_frame_at": None, "first_audio_at": None, "sent": 0,
    }
    async with connect(target.ws_url, open_timeout=10, max_size=8 * 1024 * 1024) as ws:
        await ws.send(wire.hello_message(device="vc_smoke", caps=["ask"]))

        async def listen() -> None:
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        state["audio_bytes"] += len(raw)
                        state["first_audio_at"] = state["first_audio_at"] or time.monotonic()
                        continue
                    message = wire.decode(raw)
                    kind = message.get("type")
                    if kind == wire.AUDIO:
                        try:
                            size = len(base64.b64decode(message.get("data", ""), validate=True))
                        except (ValueError, TypeError):
                            size = 0
                        state["audio_bytes"] += size
                        state["first_audio_at"] = state["first_audio_at"] or time.monotonic()
                    elif kind == wire.SPEAK:
                        state["speak"] += 1
                    elif kind == wire.ASK:
                        question_id = message.get("question_id")
                        if isinstance(question_id, str) and question_id:
                            state["asks"].append(question_id)
                            await ws.send(wire.answer_message(question_id, "yes, first one today"))
                            state["answered_ids"].add(question_id)
            except ConnectionClosed:
                pass

        listener = asyncio.create_task(listen())
        loop = asyncio.get_running_loop()
        started = loop.time()
        next_at = started
        seq = 0
        try:
            while loop.time() - started < seconds:
                seq += 1
                jpeg = frame_files[(seq - 1) % len(frame_files)] if frame_files else generated_jpeg(seq)
                await ws.send(wire.capture_packet(
                    t=time.time(), jpeg=jpeg, gps_speed=0.05,
                    accel={"x": 0.01, "y": -0.12, "z": 0.98},
                ))
                state["first_frame_at"] = state["first_frame_at"] or time.monotonic()
                state["sent"] += 1
                next_at += 1.5
                await asyncio.sleep(max(0.0, next_at - loop.time()))
            # Allow a message already in flight to arrive before closing the phone.
            await asyncio.sleep(1.0)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    # Parsing an answer may be asynchronous. Poll only questions this run answered.
    if state["answered_ids"]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            questions = rows(await get_json(client, "/api/questions?limit=50"))
            confirmed = {q.get("id") for q in questions if q.get("status") == "answered"}
            if state["answered_ids"] <= confirmed:
                state["answers_confirmed"] = True
                break
            await asyncio.sleep(0.5)
        else:
            state["answers_confirmed"] = False
    else:
        state["answers_confirmed"] = True
    return state


def report(ok: bool, label: str, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label} — {detail}", flush=True)
    return ok


async def run(target: Target, seconds: float, frame_files: list[bytes]) -> int:
    outcomes: list[bool] = []
    ok, detail = await check_tls_auth(target)
    outcomes.append(report(ok, "TLS + auth", detail))

    async with httpx.AsyncClient(
        base_url=target.api_base + "/", headers=auth_headers(target), timeout=15,
        follow_redirects=False,
    ) as client:
        try:
            before = await get_json(client, "/api/status")
            outcomes.append(report(True, "API reachability", "GET /api/status returned JSON over HTTPS"))
        except Exception as exc:
            report(False, "API reachability", f"{type(exc).__name__}: {exc}")
            return 1

        try:
            decisions_before = len(rows(await get_json(client, "/api/decisions?limit=200")))
            recaps_before = {r.get("id") for r in rows(await get_json(client, "/api/recaps?limit=200"), "recaps")}
            voice = await stream(target, client, seconds, frame_files)
            after = await get_json(client, "/api/status")
            decisions_after = len(rows(await get_json(client, "/api/decisions?limit=200")))
            verdict = stream_verdict(before, after, decisions_before, decisions_after)
            detail = (f"{verdict.ticks} ticks, AI coverage {verdict.coverage:.2f}, "
                      f"decision {'yes' if verdict.decision_produced else 'no'}")
            if verdict.reason:
                detail += f"; {verdict.reason}"
            outcomes.append(report(verdict.passed, "Stream", detail))
        except Exception as exc:
            outcomes.append(report(False, "Stream", f"{type(exc).__name__}: {exc}"))
            return 1

        latency = None
        if voice["first_audio_at"] is not None and voice["first_frame_at"] is not None:
            latency = voice["first_audio_at"] - voice["first_frame_at"]
        voice_ok = bool(voice["answers_confirmed"])
        voice_detail = (f"asks {len(voice['asks'])}, answered {len(voice['answered_ids'])}, "
                        f"speak messages {voice['speak']}, audio {voice['audio_bytes']} bytes")
        if latency is not None:
            voice_detail += f", first-frame-to-audio {latency:.2f}s"
        elif voice["asks"]:
            voice_detail += ", no audio payload received"
        else:
            voice_detail += "; no ask arrived"
        if not voice_ok:
            voice_detail += "; answered question did not become answered in /api/questions"
        outcomes.append(report(voice_ok, "Voice loop", voice_detail))

        session = after.get("session") if isinstance(after, dict) else None
        session_id = session.get("id") if isinstance(session, dict) else None
        deadline = time.monotonic() + 40
        recap_id = None
        while time.monotonic() < deadline:
            try:
                current = rows(await get_json(client, "/api/recaps?limit=200"), "recaps")
                match = next((r for r in current if r.get("id") not in recaps_before and
                              (session_id is None or r.get("session_id") == session_id)), None)
                if match:
                    recap_id = match.get("id")
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)
        outcomes.append(report(bool(recap_id), "Session log",
                               f"recap {recap_id} for auto session {session_id}"
                               if recap_id else "no recap for this auto session within 40s"))
    return 0 if all(outcomes) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test one hosted VC glasses backend")
    parser.add_argument("url", help="wss://DOMAIN/t/NAME/ws/glasses?token=TOKEN")
    parser.add_argument("--frames", type=Path, help="directory of JPEG capture frames")
    parser.add_argument("--seconds", type=float, default=60.0, help="stream duration (default 60)")
    args = parser.parse_args()
    try:
        if args.seconds <= 0:
            raise ValueError("--seconds must be greater than zero")
        target = parse_target(args.url)
        frames = load_frames(args.frames)
    except ValueError as exc:
        parser.error(str(exc))
    raise SystemExit(asyncio.run(run(target, args.seconds, frames)))


if __name__ == "__main__":
    main()
