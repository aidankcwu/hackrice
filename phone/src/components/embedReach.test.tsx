import { existsSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import days from "../../fixtures/month/days.json";
import { Analysis } from "@/components/analysis/Analysis";
import { LeverSheet } from "@/components/analysis/LeverSheet";
import { Calendar } from "@/components/calendar/Calendar";
import type { Month } from "@/lib/month/types";
import { monthFindings, operatingFor } from "@/lib/operating";
import { RULES, type RuleId } from "@/lib/rules";
import type { MonthState } from "@/lib/useMonth";
import { ShellFrame } from "./Shell";

/**
 * The embed reachability audit (docs/DEMO_UI_PRD.md, "Analysis tab"): nothing on
 * the embedded /analysis or /calendar may lead to these screens. The routes stay
 * in the repo; the embed just never links to them.
 */
const UNREACHABLE = ["treatments", "tests", "biomarkers", "devices", "library", "sources", "claims", "concierge", "find", "settings"];

vi.mock("next/navigation", () => ({ useRouter: () => ({ back() {}, push() {}, replace() {} }) }));

// The seeded month, loaded: renderToStaticMarkup runs no effects, so the real hook would stay on its loading state.
vi.mock("@/lib/useMonth", () => ({
  useMonth: (): MonthState => {
    const month = days as unknown as Month;
    const findings = monthFindings(month.days);
    return { loaded: true, month, error: null, findings, operating: month.days.map((_, i) => operatingFor(month.days, i, findings)) };
  },
}));

/** Every href, action and formaction in the markup whose path is one of the unreachable screens. */
function unreachableTargets(html: string): string[] {
  return [...html.matchAll(/\b(?:href|action|formaction)="([^"]*)"/gi)]
    .map((m) => m[1])
    .filter((target) => {
      const path = new URL(target.replaceAll("&amp;", "&"), "http://x").pathname.replace(/\/+$/, "");
      return UNREACHABLE.some((name) => path === `/${name}` || path.startsWith(`/${name}/`));
    });
}

const embedded = (screen: "analysis" | "calendar", page: React.ReactNode) =>
  renderToStaticMarkup(
    <ShellFrame screen={screen} embedded>
      {page}
    </ShellFrame>,
  );

describe("embed reachability: rendered links", () => {
  it("/analysis with the seeded month links nowhere unreachable and has no menu", () => {
    const html = embedded("analysis", <Analysis />);
    expect(html).toContain("What the reds cost"); // the data state rendered, not the loading one
    expect(html).toContain('id="lever-title"'); // the lever sheet's contents are in the markup
    expect(html).not.toContain('aria-label="Menu"');
    expect(unreachableTargets(html)).toEqual([]);
  });

  it("every rule's lever sheet links nowhere unreachable", () => {
    for (const rule of Object.keys(RULES) as RuleId[]) {
      const html = renderToStaticMarkup(<LeverSheet rule={rule} open onClose={() => {}} />);
      expect(html, rule).toContain(RULES[rule].name.replaceAll("&", "&amp;"));
      expect(unreachableTargets(html), rule).toEqual([]);
    }
  });

  it("/calendar with the seeded month links nowhere unreachable and has no menu", () => {
    const html = embedded("calendar", <Calendar />);
    expect(html).toContain('role="img"');
    expect(html).not.toContain('aria-label="Menu"');
    expect(unreachableTargets(html)).toEqual([]);
  });

  it("the check itself catches such a link", () => {
    expect(unreachableTargets('<a href="/treatments?embed=1">x</a><a href="/analysis">y</a><form action="/settings/"></form>')).toEqual([
      "/treatments?embed=1",
      "/settings/",
    ]);
  });
});

/**
 * Buttons navigate in code (`router.push`), which markup cannot show. So every
 * module the two pages load, followed through their imports, may not name an
 * unreachable screen. Two files are read by the render tests above instead:
 * the Shell (its Find pill and tab bar exist only in the non-embedded branch)
 * and the screen table itself. The Menu is not followed at all: embedded, the
 * Shell never mounts it and renders no button that opens it.
 */
const SRC = resolve(__dirname, "..");
const NOT_SCANNED = ["components/Shell.tsx", "lib/screens.ts"];
const NOT_FOLLOWED = ["components/menu/"];

function resolveImport(from: string, spec: string): string | null {
  const base = spec.startsWith("@/") ? join(SRC, spec.slice(2)) : spec.startsWith(".") ? resolve(dirname(from), spec) : null;
  if (!base) return null; // a package
  for (const file of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (/\.tsx?$/.test(file) && existsSync(file)) return file;
  }
  return null; // JSON, CSS
}

function moduleGraph(entries: string[]): string[] {
  const seen = new Set<string>();
  const queue = entries.map((entry) => join(SRC, entry));
  while (queue.length) {
    const file = queue.pop()!;
    const rel = relative(SRC, file);
    if (seen.has(file) || NOT_FOLLOWED.some((dir) => rel.startsWith(dir))) continue;
    seen.add(file);
    const source = readFileSync(file, "utf8");
    for (const [, spec] of source.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)) {
      const next = resolveImport(file, spec);
      if (next) queue.push(next);
    }
  }
  return [...seen].map((file) => relative(SRC, file)).sort();
}

describe("embed reachability: code paths", () => {
  const graph = moduleGraph(["app/layout.tsx", "app/analysis/page.tsx", "app/calendar/page.tsx"]);
  const names = UNREACHABLE.join("|");
  const mentions = new RegExp(`["'\`]/(?:${names})(?=[/?#"'\`])|SCREENS\\.(?:${names})\\b|SCREENS\\[["'](?:${names})["']\\]`);

  it("follows the pages into their components", () => {
    expect(graph).toEqual(expect.arrayContaining(["components/analysis/Analysis.tsx", "components/calendar/DayStrip.tsx", "components/ui/Button.tsx"]));
    expect(graph.some((file) => file.startsWith("components/menu/"))).toBe(false);
  });

  it("no module the embedded pages load names an unreachable screen", () => {
    const offenders = graph.filter((file) => !NOT_SCANNED.includes(file) && mentions.test(readFileSync(join(SRC, file), "utf8")));
    expect(offenders).toEqual([]);
  });

  it("the pattern catches the ways a screen gets named", () => {
    for (const line of ['router.push("/tests")', "href={`/library?x=1`}", "SCREENS.find.href", 'SCREENS["settings"].href']) {
      expect(mentions.test(line), line).toBe(true);
    }
    expect(mentions.test('request("/api/tests")')).toBe(false);
  });
});
