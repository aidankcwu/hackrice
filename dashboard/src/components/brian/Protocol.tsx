"use client";
import { useCallback } from "react";
import { Check, Download, Eye, RotateCcw, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import { buildProtocolGrid, dayAsUtc, PROTOCOL_GRID_DAYS, protocolStatusLabel } from "@/lib/protocol";
import type { ProtocolGrid } from "@/lib/protocol";
import { usePoll } from "@/lib/usePoll";
import { T } from "@/lib/tokens";
import { H2, Panel } from "./Panel";

/* Protocol: per item, the last 14 days of seen / done / missed / waiting.
 * Green is a dose kept (the glasses saw it, or you marked it), red a window
 * that closed without one; every cell also carries an icon and a label, so
 * colour is never the only cue. A day the backend has no row for is "Not
 * scheduled" -- the grid never fills a gap with a status nobody recorded. */

interface Look { bg: string; fg: string; Icon: LucideIcon | null }
const LOOK: Record<string, Look> = {
  seen: { bg: T.earnSoft, fg: T.earn, Icon: Eye },
  done: { bg: T.earnSoft, fg: T.earn, Icon: Check },
  missed: { bg: T.costSoft, fg: T.cost, Icon: X },
  waiting: { bg: T.surface2, fg: T.muted, Icon: null },
  undone: { bg: T.surface2, fg: T.muted, Icon: RotateCcw },
};
const look = (status: string | null): Look => (status === null ? { bg: "transparent", fg: T.line, Icon: null } : LOOK[status] ?? LOOK.waiting);

const LEGEND: Array<string | null> = ["seen", "done", "missed", "waiting", "undone", null];

const longDate = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const narrowWeekday = new Intl.DateTimeFormat(undefined, { weekday: "narrow", timeZone: "UTC" });
const dayOfMonth = new Intl.DateTimeFormat(undefined, { day: "numeric", timeZone: "UTC" });

function Dot({ status, label }: { status: string | null; label?: string }) {
  const { bg, fg, Icon } = look(status);
  return (
    <span
      className="inline-flex h-6 w-6 items-center justify-center rounded-full align-middle"
      style={{ background: bg, color: fg }}
      {...(label ? { role: "img", "aria-label": label, title: label, "data-status": status ?? "none" } : { "aria-hidden": true })}
    >
      {Icon ? <Icon size={14} strokeWidth={2.5} aria-hidden="true" /> : status === null ? <span className="h-1 w-1 rounded-full" style={{ background: T.line }} /> : null}
    </span>
  );
}

export type ProtocolViewState =
  | { kind: "loading" }
  | { kind: "offline" }
  | { kind: "ready"; grid: ProtocolGrid; stale: boolean };

function GridTable({ grid }: { grid: ProtocolGrid }) {
  return (
    <div className="overflow-x-auto">
      <table className="border-separate" style={{ borderSpacing: 4 }}>
        <caption className="sr-only">
          Protocol, {longDate.format(dayAsUtc(grid.days[0]))} to {longDate.format(dayAsUtc(grid.today))}
        </caption>
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">Item</span></th>
            {grid.days.map((day) => {
              const isToday = day === grid.today;
              return (
                <th key={day} scope="col" className="p-0 text-center" style={{ fontSize: 12, color: isToday ? T.ink : T.muted, fontWeight: isToday ? 700 : 400 }}>
                  <span aria-hidden="true" className="block">{narrowWeekday.format(dayAsUtc(day))}</span>
                  <span aria-hidden="true" className="block">{dayOfMonth.format(dayAsUtc(day))}</span>
                  <span className="sr-only">{longDate.format(dayAsUtc(day))}{isToday ? ", today" : ""}</span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((row) => (
            <tr key={row.id}>
              <th scope="row" className="pr-3 text-left align-middle font-normal">
                <span className="block text-sm font-semibold whitespace-nowrap" style={{ color: T.ink }}>{row.name}</span>
                <span className="block whitespace-nowrap" style={{ color: T.muted, fontSize: 12 }}>{row.window_start}–{row.window_end}</span>
              </th>
              {row.cells.map((cell) => (
                <td key={cell.day} className="p-0 text-center align-middle">
                  <Dot status={cell.status} label={`${row.name}, ${longDate.format(dayAsUtc(cell.day))}: ${protocolStatusLabel(cell.status)}`} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProtocolView({ state, exportHref }: { state: ProtocolViewState; exportHref: string }) {
  const muted = (text: string, extra = "") => <p className={`m-0 text-sm ${extra}`} style={{ color: T.muted }}>{text}</p>;
  return (
    <Panel id="protocol" labelledBy="protocol-title">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <H2 id="protocol-title" sub={`The last ${PROTOCOL_GRID_DAYS} days. Seen means the glasses saw it. Done means you marked it.`}>
          Protocol
        </H2>
        <a
          href={exportHref}
          className="tile inline-flex h-11 shrink-0 items-center gap-1 rounded-full px-4 text-sm font-medium no-underline"
          style={{ background: T.bg, color: T.ink }}
        >
          <Download size={16} aria-hidden="true" /> Export CSV
        </a>
      </div>
      {state.kind === "loading" && muted("Loading the protocol…")}
      {state.kind === "offline" && muted("The backend is unreachable, so no statuses are shown.")}
      {state.kind === "ready" && state.grid.rows.length === 0 && muted("No protocol items yet. Add one in Bryan on your phone.")}
      {state.kind === "ready" && state.grid.rows.length > 0 && (
        <>
          <GridTable grid={state.grid} />
          <ul className="m-0 mt-4 flex list-none flex-wrap gap-x-4 gap-y-2 p-0">
            {LEGEND.map((status) => (
              <li key={status ?? "none"} className="flex items-center gap-2 text-sm" style={{ color: T.muted }}>
                <Dot status={status} />
                {protocolStatusLabel(status)}
              </li>
            ))}
          </ul>
          {state.stale && muted("The backend stopped answering. This is the last grid it sent.", "mt-3")}
        </>
      )}
    </Panel>
  );
}

export function Protocol() {
  const poll = usePoll<ProtocolGrid>(
    useCallback(async () => {
      const [today, history] = await Promise.all([api.protocolToday(), api.protocolHistory(PROTOCOL_GRID_DAYS)]);
      return { data: buildProtocolGrid(today, history, PROTOCOL_GRID_DAYS), mock: false };
    }, []),
    10000,
  );
  const state: ProtocolViewState = poll.data
    ? { kind: "ready", grid: poll.data, stale: poll.error !== undefined }
    : poll.error
      ? { kind: "offline" }
      : { kind: "loading" };
  return <ProtocolView state={state} exportHref={api.protocolExportUrl(PROTOCOL_GRID_DAYS)} />;
}
