#!/usr/bin/env python3
"""Live-demo dependency and network preflight. Never prints secret values."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from pipeline.capture.tts import ElevenLabsTTS
from pipeline.config import Settings


Check = tuple[str, str, str]


def result(name: str, ok: bool, detail: str) -> Check:
    return name, "PASS" if ok else "FAIL", detail


def secret_detail(value: str | None) -> str:
    return f"set (len {len(value)})" if value else "not set"


_TOKENISH = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|[A-Za-z0-9_\-]{24,})")


def safe_error(exc: BaseException) -> str:
    """Exception type, HTTP status if any, and a redacted, truncated message.

    Provider auth errors echo the submitted key back; this script may be on a
    projector. Anything token-shaped is replaced and the text is capped.
    """
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    message = _TOKENISH.sub("<redacted>", str(exc)).replace("\n", " ")[:120]
    head = type(exc).__name__ + (f" {status}" if status else "")
    return f"{head}: {message}" if message else head


def lan_ip() -> str | None:
    try:
        value = subprocess.run(
            ["ipconfig", "getifaddr", "en0"], capture_output=True, text=True,
            timeout=2, check=False,
        ).stdout.strip()
        if value:
            return value
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        output = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=2, check=False
        ).stdout
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "inet" and fields[1] != "127.0.0.1":
                return fields[1]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return None


def port_check(port: int) -> Check:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return result("Port", True, f"{port} is free")
        except PermissionError:
            return "Port", "SKIP", "socket checks are blocked by this environment"
        except OSError:
            pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            if response.status == 200:
                return result("Port", True, f"{port} already serves /api/status")
    except Exception:
        pass
    return result("Port", False, f"{port} is occupied by another service")


async def provider_checks(settings: Settings, offline: bool) -> list[Check]:
    if offline:
        return [(name, "SKIP", "--offline") for name in ("Gemini", "OpenAI", "ElevenLabs")]
    checks: list[Check] = []
    try:
        from longevity.vlm import build_client
        frame = next(iter(sorted((Path("../data/corpus_smoke")).glob("*.jpg"))))
        client = build_client("gemini")
        started = time.perf_counter()
        tagged = await client.tag(frame.read_bytes())
        ms = (time.perf_counter() - started) * 1000
        await client.aclose()
        checks.append(result("Gemini", isinstance(tagged, dict) and ms < 3000,
                             f"{ms:.0f} ms, scene={tagged.get('scene') if isinstance(tagged, dict) else None}"))
    except Exception as exc:
        checks.append(result("Gemini", False, safe_error(exc)))
    try:
        from openai import AsyncOpenAI
        started = time.perf_counter()
        response = await AsyncOpenAI(api_key=settings.openai_api_key).responses.create(
            model=settings.t1_model, input="Reply with the single word OK"
        )
        ms = (time.perf_counter() - started) * 1000
        checks.append(result("OpenAI", bool(response.output_text), f"{ms:.0f} ms"))
    except Exception as exc:
        checks.append(result("OpenAI", False, safe_error(exc)))
    try:
        started = time.perf_counter()
        audio = await ElevenLabsTTS(settings.elevenlabs_api_key or "", settings.elevenlabs_voice_id).synthesize("Preflight check.")
        ms = (time.perf_counter() - started) * 1000
        checks.append(result("ElevenLabs", bool(audio), f"{len(audio)} bytes, {ms:.0f} ms"))
    except Exception as exc:
        checks.append(result("ElevenLabs", False, safe_error(exc)))
    return checks


async def run(args: argparse.Namespace) -> int:
    env_path = Path(".env")
    load_dotenv(env_path, override=False)
    settings = Settings()
    checks = [result(".env", env_path.is_file(), str(env_path.resolve()))]
    for label, value in (
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY")),
        ("ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
    ):
        checks.append(result(label, bool(value), secret_detail(value)))
    checks.extend(await provider_checks(settings, args.offline))
    ip = lan_ip()
    checks.append(result("LAN IP", ip is not None,
                         f"ws://{ip}:{args.port}/ws/glasses" if ip else "not found"))
    checks.append(port_check(args.port))
    dashboard_env = Path("../dashboard/.env.local")
    base = None
    if dashboard_env.exists():
        for line in dashboard_env.read_text().splitlines():
            if line.startswith("NEXT_PUBLIC_API_BASE="):
                base = line.partition("=")[2].strip()
                break
    matches = bool(base and f":{args.port}" in base)
    checks.append(result("Dashboard", matches,
                         f"NEXT_PUBLIC_API_BASE port {'matches' if matches else 'does not match'} {args.port}"))
    width = max(len(name) for name, _, _ in checks)
    for name, status, detail in checks:
        print(f"{name:<{width}}  {status:<4}  {detail}")
    return 1 if any(status == "FAIL" for _, status, _ in checks) else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--port", type=int, default=8010)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
