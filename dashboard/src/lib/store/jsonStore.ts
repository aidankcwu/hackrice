/**
 * Tiny JSON-file store for the per-user state the engine does not own:
 * PVT runs, adherence logs, adaptive targets. One file per collection under
 * `data/brian/` (the repo's `.gitignore` already ignores `data/`), written
 * atomically (temp file + rename) and serialised per collection so two
 * requests cannot interleave a read-modify-write.
 *
 * `BRIAN_DATA_DIR` overrides the directory (tests point it at a temp dir).
 */
import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";

export function dataDir(): string {
  const configured = process.env.BRIAN_DATA_DIR?.trim();
  return configured || path.join(process.cwd(), "data", "brian");
}

const fileFor = (name: string): string => path.join(dataDir(), `${name}.json`);

const isMissing = (e: unknown): boolean => (e as NodeJS.ErrnoException)?.code === "ENOENT";

export async function readJson<T>(name: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await fs.readFile(fileFor(name), "utf8")) as T;
  } catch (e) {
    if (isMissing(e)) return fallback;
    throw e;
  }
}

export async function writeJson<T>(name: string, value: T): Promise<void> {
  const file = fileFor(name);
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(value, null, 2), "utf8");
  await fs.rename(tmp, file);
}

// One promise chain per collection: each update waits for the previous one.
const chains = new Map<string, Promise<unknown>>();

export async function updateJson<T>(name: string, fallback: T, fn: (current: T) => T | Promise<T>): Promise<T> {
  const previous = chains.get(name) ?? Promise.resolve();
  const run = previous.then(
    async () => {
      const next = await fn(await readJson(name, fallback));
      await writeJson(name, next);
      return next;
    },
    async () => {
      // A failed earlier update must not poison the chain.
      const next = await fn(await readJson(name, fallback));
      await writeJson(name, next);
      return next;
    },
  );
  chains.set(name, run);
  return run;
}
