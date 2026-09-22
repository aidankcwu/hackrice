import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ProtocolView } from "@/components/brian/Protocol";
import type { ProtocolViewState } from "@/components/brian/Protocol";
import { api } from "./api";
import { protocolCsvFixture, protocolTodayFixture } from "./protocol.fixture";
import { buildProtocolGrid, lastDays, parseProtocolCsv } from "./protocol";

const exportHref = api.protocolExportUrl(14);
const render = (state: ProtocolViewState) => renderToStaticMarkup(createElement(ProtocolView, { state, exportHref }));
/** Every grid cell's status, in document order (legend swatches carry none). */
const statuses = (html: string) => [...html.matchAll(/data-status="([^"]+)"/g)].map((m) => m[1]);

describe("protocol CSV", () => {
  it("reads rows by column, keeps a quoted comma, and nulls empty cells", () => {
    const rows = parseProtocolCsv(protocolCsvFixture);
    expect(rows).toHaveLength(8);
    expect(rows[3]).toMatchObject({ day: "2026-09-21", item_id: "pi_omega", name: "Omega-3, with food", status: "missed", seen_t: null, evidence_ref: null });
    expect(rows[0]).toMatchObject({ seen_t: 1758353700, evidence_ref: "d_0003/f_00000410" });
  });

  it("anchors 14 days on the backend's today, oldest first", () => {
    expect(lastDays("2026-09-22", 14)).toEqual([
      "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15",
      "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22",
    ]);
  });
});

describe("Protocol panel", () => {
  const grid = buildProtocolGrid(protocolTodayFixture, parseProtocolCsv(protocolCsvFixture));

  it("renders the 14-day grid from a fixture with 3 days of statuses", () => {
    const html = render({ kind: "ready", grid, stale: false });
    const cells = statuses(html);
    expect(cells).toHaveLength(3 * 14);
    // Earliest window first; the 11 days before the items existed have no row.
    const row = (i: number) => cells.slice(i * 14, (i + 1) * 14);
    expect(row(0)).toEqual([...Array(11).fill("none"), "seen", "done", "seen"]);
    expect(row(1)).toEqual([...Array(11).fill("none"), "none", "missed", "waiting"]);
    expect(row(2)).toEqual([...Array(11).fill("none"), "missed", "undone", "waiting"]);
    expect(html).toMatch(/aria-label="Morning dose, [^"]+: Seen"/);
    expect(html).toMatch(/aria-label="Omega-3, with food, [^"]+: Not scheduled"/);
    expect(html).toMatch(/aria-label="Evening dose, [^"]+: Missed"/);
    expect(html).toContain(`href="${exportHref}"`);
    expect(exportHref).toMatch(/\/api\/protocol\/export\.csv\?days=14$/);
    expect(html).toContain("Export CSV");
  });

  it("shows no status at all when the backend is unreachable", () => {
    const html = render({ kind: "offline" });
    expect(statuses(html)).toEqual([]);
    expect(html).toContain("The backend is unreachable, so no statuses are shown.");
    expect(html).toContain("Export CSV");
  });

  it("says so when there are no items, rather than drawing an empty grid", () => {
    const html = render({ kind: "ready", grid: buildProtocolGrid({ day: "2026-09-22", items: [] }, []), stale: false });
    expect(statuses(html)).toEqual([]);
    expect(html).toContain("No protocol items yet.");
  });
});
