/**
 * `POST /api/narrate` — `{annotations, narrator_prompts, days}` from the
 * engine payload in, one sentence per annotation out. An LLM (Gemini or
 * OpenAI, whichever key exists; temperature 0) may phrase each sentence; any
 * output carrying a number that is not in the annotation JSON is rejected
 * and the template sentence is returned instead. With no key configured the
 * templates are returned directly.
 */
import { NextResponse } from "next/server";
import { isAnnotation, narrate, type Annotation } from "@/lib/narrator";
import { llmSentence, narratorProvider } from "@/lib/narrator-llm";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (body: unknown, status = 200): NextResponse => NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function POST(req: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "request body must be JSON" }, 400);
  }
  if (body === null || typeof body !== "object") return json({ error: "body must be an object {annotations, narrator_prompts, days}" }, 400);
  const b = body as Record<string, unknown>;
  if (!Array.isArray(b.annotations)) return json({ error: "annotations must be an array" }, 400);
  const annotations: Annotation[] = [];
  for (const a of b.annotations) {
    if (!isAnnotation(a)) return json({ error: "each annotation needs kind run | contrast | extreme" }, 400);
    annotations.push(a);
  }
  const prompts = Array.isArray(b.narrator_prompts) ? b.narrator_prompts.map((p) => (typeof p === "string" ? p : undefined)) : [];
  const days = Array.isArray(b.days) && b.days.every((d) => typeof d === "string") ? (b.days as string[]) : undefined;

  const { provider, model } = await narratorProvider();
  const sentences = await narrate(annotations, prompts, { days }, await llmSentence());
  return json({ sentences, provider, model: model ?? null });
}

export async function GET(): Promise<NextResponse> {
  return json(await narratorProvider());
}
