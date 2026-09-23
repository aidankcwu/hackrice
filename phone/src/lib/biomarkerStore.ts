"use client";

/**
 * The wearer's own biomarker results, kept on this phone only (`localStorage`).
 * The seeded panel in `content/biomarkers.ts` is a placeholder; a typed-in result
 * for the same marker takes its place on the screen ("merged over" the seeded
 * values), and the "seeded" chip goes away for that row.
 */
import { useCallback, useSyncExternalStore } from "react";
import type { Biomarker } from "@/content/types";

/** `localStorage` key: a JSON array of results, oldest first. */
export const BIOMARKERS_KEY = "brian.biomarkers";
/** Dispatched on this window after every write, so an open screen refreshes. */
export const BIOMARKERS_EVENT = "brian:biomarkers";

/** One result the wearer typed in. `date` is an ISO day, YYYY-MM-DD. */
export interface BiomarkerResult {
  id: string;
  value: number;
  date: string;
}

/** What a row shows: the wearer's latest result when there is one, else the seeded panel's. */
export interface BiomarkerReading {
  value: number | null;
  lastTested: string | null;
  /** True when the value comes from the seeded panel, not the wearer. */
  seeded: boolean;
}

const EMPTY: BiomarkerResult[] = [];

/** This page's writes, for a browser that refuses storage (private window, blocked site data). */
let memory: BiomarkerResult[] = EMPTY;
/** The last raw string parsed, so the snapshot keeps one array reference until storage changes. */
let cachedRaw: string | null = null;
let cached: BiomarkerResult[] = EMPTY;

function isResult(value: unknown): value is BiomarkerResult {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.value === "number" &&
    Number.isFinite(record.value) &&
    typeof record.date === "string"
  );
}

function parse(raw: string | null): BiomarkerResult[] {
  if (!raw) return EMPTY;
  try {
    const data: unknown = JSON.parse(raw);
    return Array.isArray(data) ? data.filter(isResult) : EMPTY;
  } catch {
    return EMPTY;
  }
}

/** Every stored result; an empty list when storage is empty, refused or unreadable. */
export function readResults(): BiomarkerResult[] {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(BIOMARKERS_KEY);
  } catch {
    return memory;
  }
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cached = parse(raw);
  }
  return cached;
}

export function writeResults(results: BiomarkerResult[]): void {
  memory = results;
  try {
    window.localStorage.setItem(BIOMARKERS_KEY, JSON.stringify(results));
  } catch {
    // Storage refused: the results last for this page.
  }
  window.dispatchEvent(new Event(BIOMARKERS_EVENT));
}

/** Appends one result, writes, and returns the new list. */
export function addResult(result: BiomarkerResult): BiomarkerResult[] {
  const next = [...readResults(), result];
  writeResults(next);
  return next;
}

/** The wearer's latest result for a marker: the latest date, then the last one added. */
export function latestResult(results: BiomarkerResult[], id: string): BiomarkerResult | undefined {
  let latest: BiomarkerResult | undefined;
  for (const result of results) {
    if (result.id === id && (!latest || result.date >= latest.date)) latest = result;
  }
  return latest;
}

/** The reading a row shows: the wearer's own result wins over the seeded placeholder. */
export function readingFor(biomarker: Biomarker, results: BiomarkerResult[]): BiomarkerReading {
  const own = latestResult(results, biomarker.id);
  if (own) return { value: own.value, lastTested: own.date, seeded: false };
  return {
    value: biomarker.seeded.value,
    lastTested: biomarker.seeded.lastTested,
    seeded: biomarker.seeded.value !== null,
  };
}

// ---------------------------------------------------------------------------
// Parsing and formatting
// ---------------------------------------------------------------------------

const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** True for a real calendar day written YYYY-MM-DD. */
export function isIsoDay(text: string): boolean {
  const match = ISO_DAY.exec(text);
  if (!match) return false;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

/** Today as an ISO day in local time. */
export function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A typed value as a number, or null when it does not parse. A comma decimal is accepted. */
export function parseValue(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** An ISO day as "14 Aug 2026"; anything else is shown as typed. */
export function formatDay(iso: string): string {
  const match = ISO_DAY.exec(iso);
  if (!match) return iso;
  const [year, month, day] = match.slice(1).map(Number);
  const name = MONTHS[month - 1];
  return name ? `${day} ${name} ${year}` : iso;
}

/** A value for the row: up to two decimals, no trailing zeros. */
export function formatValue(value: number): string {
  return String(Math.round(value * 100) / 100);
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(BIOMARKERS_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(BIOMARKERS_EVENT, onChange);
  };
}

const serverSnapshot = () => EMPTY;

/**
 * The stored results as `{ results, add }`. The server render and the first
 * client render see none (the seeded panel shows), then the stored ones, so
 * hydration never mismatches. Follows the "storage" event and this page's writes.
 */
export function useBiomarkerResults(): { results: BiomarkerResult[]; add: (result: BiomarkerResult) => void } {
  const results = useSyncExternalStore(subscribe, readResults, serverSnapshot);
  const add = useCallback((result: BiomarkerResult) => {
    addResult(result);
  }, []);
  return { results, add };
}
