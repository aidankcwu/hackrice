/**
 * Spawn helper for the engine's other CLI branches (`--registry`,
 * `--forecast`) and the small wrapper scripts next to it. Same conventions as
 * `engine.ts` (python command, UTF-8, timeout, EngineError).
 */
import "server-only";
import { spawn } from "node:child_process";
import path from "node:path";
import { EngineError, pythonCommand } from "./engine";

export const SCORE_DIR = path.join(process.cwd(), "lib", "score");
export const SCORE_SCRIPT = path.join(SCORE_DIR, "brian_score.py");

export function runPythonScript(script: string, args: string[], stdin: string, timeoutMs: number): Promise<string> {
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
      reject(new EngineError(`${path.basename(script)} timed out after ${timeoutMs} ms`, err));
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
      else reject(new EngineError(`${path.basename(script)} exited with code ${code}`, err));
    });
    child.stdin.on("error", () => {
      /* the close handler reports the real failure */
    });
    if (stdin) child.stdin.write(stdin);
    child.stdin.end();
  });
}
