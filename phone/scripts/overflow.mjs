// Sweep the embedded screens at a phone width for page-level horizontal overflow.
//
//   node scripts/overflow.mjs [out.txt] [width]
//
// For each route, loaded with ?embed=1 against PHONE_URL: the page's scrollWidth
// against the viewport, and every element whose box pokes past either edge while
// not sitting inside its own horizontal scroller (those are the culprits). On
// /analysis it also checks, for every range, that the latest day's column, the
// hour axis and the chart caption sit fully on screen, and that no day column is
// cut at either edge of the chart's scroller. Exits 1 on any failure.
import { writeFileSync } from "node:fs";
import { sleep, withPhone } from "./chrome.mjs";

const ROUTES = ["/calendar", "/analysis", "/treatments", "/tests", "/biomarkers", "/devices", "/library", "/find", "/concierge", "/sources", "/claims", "/settings"];
const [out, width = "390"] = process.argv.slice(2);
const W = Number(width);

const PAGE_CHECK = `(() => {
  const W = document.documentElement.clientWidth;
  const scrolls = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true; } return false; };
  const culprits = [...document.body.querySelectorAll("*")]
    .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > W + 0.5 || r.left < -0.5) && !scrolls(el); })
    .slice(0, 5)
    .map((el) => el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\\s+/).slice(0, 3).join(".") : ""));
  return { scrollWidth: document.scrollingElement.scrollWidth, culprits };
})()`;

const CHART_CHECK = `(() => {
  const W = document.documentElement.clientWidth;
  const chart = document.querySelector("[data-chart]");
  const box = chart?.getBoundingClientRect();
  const days = [...document.querySelectorAll("[data-chart] [role=group]")].map((d) => d.getBoundingClientRect());
  const last = days.at(-1);
  const caption = document.querySelector("[data-chart-caption]")?.getBoundingClientRect();
  const hours = document.querySelector("[data-chart-hours]")?.getBoundingClientRect();
  const on = (r) => !!r && r.left >= -0.5 && r.right <= W + 0.5;
  // A day the scroller shows any of must be shown whole: a column cut at either edge is clipping.
  const cut = box ? days.filter((r) => r.right > box.left + 0.5 && r.left < box.right - 0.5 && (r.left < box.left - 0.5 || r.right > box.right + 0.5)).length : days.length;
  return { days: days.length, latestOnScreen: on(last) && (!box || last.right <= box.right + 0.5), captionOnScreen: on(caption), hoursOnScreen: on(hours), chartOnScreen: on(box), cut };
})()`;

const lines = [`Horizontal overflow sweep at ${W} px, embed=1, ${new Date().toISOString()}`, ""];
let failed = false;

await withPhone({ width: W }, async (page) => {
  for (const route of ROUTES) {
    await page.open(`${route}?embed=1`);
    const { scrollWidth, culprits } = await page.evaluate(PAGE_CHECK);
    const ok = scrollWidth <= W && culprits.length === 0;
    failed ||= !ok;
    lines.push(`${ok ? "ok  " : "FAIL"} ${route.padEnd(12)} scrollWidth ${scrollWidth}${culprits.length ? `  past the edge: ${culprits.join(", ")}` : ""}`);

    if (route === "/analysis") {
      for (const label of ["This week", "2 weeks", "30 days"]) {
        await page.evaluate(`[...document.querySelectorAll("[role=radio], button")].find((b) => b.textContent.trim() === ${JSON.stringify(label)})?.click()`);
        await sleep(400);
        const chart = await page.evaluate(CHART_CHECK);
        const again = await page.evaluate(PAGE_CHECK);
        const chartOk = chart.days > 0 && chart.latestOnScreen && chart.captionOnScreen && chart.hoursOnScreen && chart.chartOnScreen && chart.cut === 0 && again.scrollWidth <= W;
        failed ||= !chartOk;
        lines.push(
          `${chartOk ? "ok  " : "FAIL"}   chart "${label}": ${chart.days} days, latest day ${chart.latestOnScreen ? "on screen" : "cut off"}, ${chart.cut} ${chart.cut === 1 ? "day" : "days"} cut at an edge, hours ${chart.hoursOnScreen ? "on screen" : "cut off"}, caption ${chart.captionOnScreen ? "on screen" : "cut off"}, scrollWidth ${again.scrollWidth}`,
        );
      }
    }
  }
});

lines.push("", failed ? "FAIL: page-level horizontal overflow found" : `PASS: no page-level horizontal overflow at ${W} px`);
const text = lines.join("\n") + "\n";
process.stdout.write(text);
if (out) writeFileSync(out, text);
process.exit(failed ? 1 : 0);
