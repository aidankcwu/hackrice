/**
 * The narrator's LLM call: one prompt in, one sentence out, temperature 0.
 * The provider is whichever key is configured — the pipeline on this machine
 * runs on OpenAI (backend/.env), Gemini is the work order's first choice —
 * and no key at all means the templates carry the whole panel.
 *
 * Keys are read from the Next process env first and then from
 * `../backend/.env`, so the dashboard uses the pipeline's key without a
 * second copy of it.
 */
import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import type { LlmSentence } from "./narrator";

const TIMEOUT_MS = 6_000;
const MAX_TOKENS = 80;

let backendEnv: Promise<Record<string, string>> | undefined;

async function readBackendEnv(): Promise<Record<string, string>> {
  const file = process.env.BRIAN_BACKEND_ENV?.trim() || path.join(process.cwd(), "..", "backend", ".env");
  try {
    const text = await fs.readFile(file, "utf8");
    const out: Record<string, string> = {};
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    }
    return out;
  } catch {
    return {};
  }
}

/** `process.env[name]`, else the backend's .env; undefined when neither has it. */
export async function secret(name: string): Promise<string | undefined> {
  const direct = process.env[name]?.trim();
  if (direct) return direct;
  backendEnv ??= readBackendEnv();
  const fromBackend = (await backendEnv)[name]?.trim();
  return fromBackend || undefined;
}

export type NarratorProvider = "gemini" | "openai" | "none";

export async function narratorProvider(): Promise<{ provider: NarratorProvider; model?: string }> {
  const forced = process.env.NARRATOR_PROVIDER?.trim();
  if (forced === "none") return { provider: "none" };
  if (forced !== "openai" && (await secret("GEMINI_API_KEY"))) return { provider: "gemini", model: process.env.NARRATOR_MODEL?.trim() || "gemini-2.5-flash-lite" };
  if (forced !== "gemini" && (await secret("OPENAI_API_KEY"))) return { provider: "openai", model: process.env.NARRATOR_MODEL?.trim() || "gpt-4o-mini" };
  return { provider: "none" };
}

async function postJson(url: string, headers: Record<string, string>, body: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function gemini(prompt: string, key: string, model: string): Promise<string | null> {
  const json = (await postJson(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,
    { "x-goog-api-key": key },
    { contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: { temperature: 0, maxOutputTokens: MAX_TOKENS } },
  )) as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }> };
  const text = json.candidates?.[0]?.content?.parts?.map((p) => p.text ?? "").join("") ?? "";
  return text || null;
}

async function openai(prompt: string, key: string, model: string): Promise<string | null> {
  const json = (await postJson(
    "https://api.openai.com/v1/chat/completions",
    { authorization: `Bearer ${key}` },
    { model, temperature: 0, max_tokens: MAX_TOKENS, messages: [{ role: "user", content: prompt }] },
  )) as { choices?: Array<{ message?: { content?: string | null } }> };
  return json.choices?.[0]?.message?.content ?? null;
}

/** An `LlmSentence` for the configured provider, or undefined when there is none (templates only). */
export async function llmSentence(): Promise<LlmSentence | undefined> {
  const { provider, model } = await narratorProvider();
  if (provider === "gemini") {
    const key = (await secret("GEMINI_API_KEY")) as string;
    return (prompt) => gemini(prompt, key, model as string);
  }
  if (provider === "openai") {
    const key = (await secret("OPENAI_API_KEY")) as string;
    return (prompt) => openai(prompt, key, model as string);
  }
  return undefined;
}
