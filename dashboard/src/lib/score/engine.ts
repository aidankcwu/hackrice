import "server-only";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import type { EnginePayload, EngineRequest } from "./types";

/**
 * Runs the Python scoring engine as a subprocess, for what still needs a local
 * run: `POST /api/score` (the brief's request-in, payload-out contract) and,
 * through `spawn.ts`, the Tonight sliders' `--forecast` and the How-it's-scored
 * page's `--registry`. The dashboard's own numbers come from the backend's
 * `/api/healthspan` (loader.ts), never from here.
 *
 * `brian_score.py` is used unchanged (`--json` reads one request on stdin).
 */

const SCORE_SCRIPT = path.join(process.cwd(), "lib", "score", "brian_score.py");

/** `BRIAN_PYTHON` overrides; otherwise `python` on Windows (the `python3` alias there is a Store stub) and `python3` elsewhere. */
export function pythonCommand(): string {
  const configured = process.env.BRIAN_PYTHON?.trim();
  if (configured) return configured;
  // The backend's uv venv already carries numpy (backend/pyproject.toml); the
  // system python3 on a fresh Mac does not. Prefer the venv when it exists so
  // `uv sync` in backend/ is the only setup step, as for the rest of the repo.
  const venv = path.join(process.cwd(), "..", "backend", ".venv", "bin", "python3");
  if (existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

export class EngineError extends Error {
  constructor(message: string, readonly stderr = "") {
    super(message);
    this.name = "EngineError";
  }
}

/**
 * Python's json.dumps writes `NaN`/`Infinity` literals, which JSON.parse rejects.
 * The engine only produces them for an attribution with fewer than 14 days, so
 * they become `null` here and `EngineEffect.beta` is typed `number | null`.
 */
export function parseEngineJson<T>(text: string): T {
  const sanitized = text.replace(/(?<![\w"])-?(?:NaN|Infinity)(?![\w"])/g, "null");
  return JSON.parse(sanitized) as T;
}

function runPython(script: string, args: string[], stdin: string, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonCommand(), [script, ...args], {
      cwd: path.dirname(script),
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      windowsHide: true,
    });
    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new EngineError(`engine timed out after ${timeoutMs} ms`, err));
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (out += chunk));
    child.stderr.on("data", (chunk: string) => (err += chunk));
    child.on("error", (e) => {
      clearTimeout(timer);
      reject(new EngineError(`could not start ${pythonCommand()}: ${e.message}`, err));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new EngineError(`engine exited with code ${code}`, err));
    });
    child.stdin.on("error", () => {
      /* the close handler reports the real failure */
    });
    child.stdin.write(stdin);
    child.stdin.end();
  });
}

/** Score one request — `python brian_score.py --json < request`. */
export async function runEngine(request: EngineRequest, timeoutMs = 10_000): Promise<EnginePayload> {
  const out = await runPython(SCORE_SCRIPT, ["--json"], JSON.stringify(request), timeoutMs);
  return parseEngineJson<EnginePayload>(out);
}
