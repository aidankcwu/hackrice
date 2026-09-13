/**
 * `python brian_score.py --registry` → the factor registry the /how-it-works
 * page renders. Nothing on that page is typed by hand: curves, grades,
 * shrink factors, sources, notes, pipeline steps and limitations all come
 * from this call. Cached per engine-file mtime, so the page is free after
 * the first render until the engine changes.
 */
import "server-only";
import { promises as fs } from "node:fs";
import { parseEngineJson } from "./engine";
import { runPythonScript, SCORE_SCRIPT } from "./spawn";
import type { EvidenceGrade } from "./types";

export interface RegistryFactor {
  key: string;
  /** Display label of the layer (`LAYER_LABELS` in the engine), e.g. "Light & clock". */
  layer: string;
  label: string;
  unit: string;
  /** (dose, hazard ratio) points, dose ascending. */
  curve: [number, number][];
  grade: EvidenceGrade;
  shrink: number;
  reference_dose: number;
  best_dose: number;
  worst_dose: number;
  higher_is_better: boolean;
  lever_step: number;
  lever_time_min: number;
  source: string;
  note: string;
}

/** `LEADING` in the engine: effect sizes plus their source line. */
export type LeadingIndicator = { source: string } & Record<string, number | string>;

export interface EngineRegistry {
  pipeline: string[];
  leading_indicators: Record<string, LeadingIndicator>;
  factors: RegistryFactor[];
  limitations: string[];
}

interface Cached {
  mtimeMs: number;
  registry: EngineRegistry;
}

const cache = new Map<string, Cached>();

/** Registry exported by `script` (default: the project engine). */
export async function loadRegistry(script: string = SCORE_SCRIPT, timeoutMs = 10_000): Promise<EngineRegistry> {
  const { mtimeMs } = await fs.stat(script);
  const hit = cache.get(script);
  if (hit && hit.mtimeMs === mtimeMs) return hit.registry;
  const out = await runPythonScript(script, ["--registry"], "", timeoutMs);
  const registry = parseEngineJson<EngineRegistry>(out);
  cache.set(script, { mtimeMs, registry });
  return registry;
}

export function clearRegistryCache(): void {
  cache.clear();
}
