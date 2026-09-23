import type { ProtocolDayRow, ProtocolStatus, ProtocolToday } from "./types";

/**
 * The adherence grid: per protocol item, what happened on each of the last 14
 * local days. Built from the two reads the backend already has (docs/API.md
 * "The protocol") -- `GET /api/protocol/today` anchors the days on the tick
 * clock, `GET /api/protocol/export.csv` carries the history. Nothing here
 * invents a status: a day the backend wrote no row for is `null`, which the
 * panel reads as "Not scheduled", never as waiting or missed.
 */

export const PROTOCOL_GRID_DAYS = 14;

export const PROTOCOL_STATUS_LABEL: Record<ProtocolStatus, string> = {
  seen: "Seen",
  done: "Done",
  missed: "Missed",
  waiting: "Waiting",
  undone: "Undone",
};

/** The word a cell reads as. A status the backend adds later is shown as sent. */
export const protocolStatusLabel = (status: string | null): string =>
  status === null ? "Not scheduled" : ((PROTOCOL_STATUS_LABEL as Record<string, string>)[status] ?? status);

export interface ProtocolCell { day: string; status: string | null; evidence_ref: string | null }
export interface ProtocolGridRow { id: string; name: string; window_start: string; window_end: string; cells: ProtocolCell[] }
export interface ProtocolGrid { today: string; days: string[]; rows: ProtocolGridRow[] }

const DAY_MS = 86_400_000;

/** `"YYYY-MM-DD"` as a UTC midnight, so day arithmetic never meets a DST shift. */
export const dayAsUtc = (day: string): Date => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};

/** The `n` local days ending at `anchor`, oldest first. */
export function lastDays(anchor: string, n: number): string[] {
  const end = dayAsUtc(anchor).getTime();
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * DAY_MS).toISOString().slice(0, 10));
}

/** CSV as Python's `csv.writer` writes it: fields quoted only when needed, `""`
 *  for a quote inside one, `\r\n` between rows. A name may hold a comma. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] === '"') { field += '"'; i++; }
      else quoted = false;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\r" || c === "\n") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      rows.push(row); row = [];
    } else field += c;
  }
  if (field !== "" || row.length > 0) { row.push(field); rows.push(row); }
  return rows.filter((r) => !(r.length === 1 && r[0] === ""));
}

/** `GET /api/protocol/export.csv` → rows, read by column name rather than position. */
export function parseProtocolCsv(text: string): ProtocolDayRow[] {
  const [header, ...body] = parseCsv(text);
  if (!header) return [];
  const col = new Map(header.map((name, i) => [name, i]));
  for (const required of ["day", "item_id", "status"]) {
    if (!col.has(required)) throw new Error(`export.csv has no ${required} column`);
  }
  const at = (row: string[], name: string): string => row[col.get(name) ?? -1] ?? "";
  const num = (s: string): number | null => (s === "" ? null : Number(s));
  return body.map((row) => ({
    day: at(row, "day"),
    item_id: at(row, "item_id"),
    name: at(row, "name"),
    kind: at(row, "kind"),
    window_start: at(row, "window_start"),
    window_end: at(row, "window_end"),
    status: at(row, "status"),
    seen_t: num(at(row, "seen_t")),
    evidence_ref: at(row, "evidence_ref") || null,
    updated_t: num(at(row, "updated_t")),
  }));
}

/** One row per item, one cell per day, earliest window first. Today's answer
 *  from `/today` wins over the CSV's copy of the same day. */
export function buildProtocolGrid(today: ProtocolToday, history: ProtocolDayRow[], n = PROTOCOL_GRID_DAYS): ProtocolGrid {
  const days = lastDays(today.day, n);
  const inRange = new Set(days);
  const items = new Map<string, Omit<ProtocolGridRow, "cells">>();
  const cells = new Map<string, ProtocolCell>();
  const put = (id: string, name: string, start: string, end: string, cell: ProtocolCell) => {
    if (!inRange.has(cell.day)) return;
    items.set(id, { id, name, window_start: start, window_end: end });
    cells.set(`${id}|${cell.day}`, cell);
  };
  for (const r of history) {
    put(r.item_id, r.name, r.window_start, r.window_end, { day: r.day, status: r.status, evidence_ref: r.evidence_ref });
  }
  for (const it of today.items) {
    put(it.id, it.name, it.window_start, it.window_end, { day: today.day, status: it.status, evidence_ref: it.evidence_ref });
  }
  const rows = [...items.values()]
    .sort((a, b) =>
      a.window_start.localeCompare(b.window_start) || a.window_end.localeCompare(b.window_end) || a.name.localeCompare(b.name))
    .map((item) => ({
      ...item,
      cells: days.map((day) => cells.get(`${item.id}|${day}`) ?? { day, status: null, evidence_ref: null }),
    }));
  return { today: today.day, days, rows };
}
