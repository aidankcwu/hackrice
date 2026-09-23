// Screenshots of every screen and sheet, light and dark, at phone sizes, into
// phone/design/shots, plus layout facts per shot in shots/index.json.
//
//   node design/shoot.mjs            (from phone/; the dev server runs on :3100 in fixtures mode)
//   node design/shoot.mjs today calendar    (only shots whose id starts with one of these)
//
// Drives headless Chrome over the DevTools protocol; no dependencies.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3100";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "shots");
const PORT = 9444;
const PHONE = { w: 390, h: 844 };
const SMALL = { w: 375, h: 667 };
const LARGE = { w: 430, h: 932 };
const THEMES = ["light", "dark"];

/** A click by visible text or aria-label, run in the page. */
const click = (text) => `(() => { const all = [...document.querySelectorAll("button, a, [role=switch]")]; const el = all.find((b) => (b.getAttribute("aria-label") || b.innerText || "").trim().replace(/\\s+/g, " ") === ${JSON.stringify(text)}) || all.find((b) => ((b.getAttribute("aria-label") || b.innerText || "").trim()).startsWith(${JSON.stringify(text)})); if (!el) return "missing: " + ${JSON.stringify(text)}; el.click(); return "ok"; })()`;

/** Every shot: id, route, optional actions (page JS run in order, 400 ms apart), sizes. */
const SHOTS = [
  { id: "today", route: "/", sizes: [PHONE, SMALL, LARGE] },
  { id: "today-how", route: "/", actions: [click("How it’s computed")] },
  { id: "today-connect", route: "/", actions: [click("Connect glasses")] },
  { id: "menu", route: "/", actions: [click("Menu")] },
  { id: "calendar-day", route: "/calendar", sizes: [PHONE, SMALL, LARGE] },
  { id: "calendar-day-rule", route: "/calendar", actions: [`(() => { const b = document.querySelector('button[aria-expanded]'); if (!b) return "no bar"; b.click(); return "ok"; })()`] },
  { id: "calendar-week", route: "/calendar", actions: [click("Week")], sizes: [PHONE, SMALL, LARGE] },
  { id: "analysis", route: "/analysis" },
  { id: "analysis-2weeks", route: "/analysis", actions: [click("2 weeks")] },
  { id: "analysis-lever", route: "/analysis", actions: [click("Biggest lever")] },
  { id: "protocol", route: "/protocol" },
  { id: "protocol-add", route: "/protocol/add" },
  { id: "protocol-action", route: "/protocol", actions: [`(() => { const r = document.querySelector('main li button'); if (!r) return "no row"; r.click(); return "ok"; })()`] },
  { id: "library", route: "/library" },
  { id: "library-detail", route: "/library/blueprint" },
  { id: "library-review", route: "/library/blueprint/review" },
  { id: "treatments", route: "/treatments" },
  { id: "treatments-detail", route: "/treatments/semaglutide" },
  { id: "tests", route: "/tests" },
  { id: "tests-pvt-intro", route: "/tests/pvt" },
  { id: "tests-pvt-run", route: "/tests/pvt", actions: [click("Start")] },
  { id: "tests-nback-run", route: "/tests/nback", actions: [click("Start")] },
  { id: "tests-dsst-run", route: "/tests/dsst", actions: [click("Start")] },
  { id: "tests-stroop-run", route: "/tests/stroop", actions: [click("Start")] },
  { id: "biomarkers", route: "/biomarkers" },
  { id: "biomarkers-add", route: "/biomarkers", actions: [click("Add result")] },
  { id: "biomarkers-baseline", route: "/biomarkers", actions: [click("Get your baseline")] },
  { id: "devices", route: "/devices" },
  { id: "devices-detail", route: "/devices/rayban-meta" },
  { id: "concierge", route: "/concierge" },
  { id: "concierge-persona", route: "/concierge", actions: [`(() => { const r = [...document.querySelectorAll("main button")].find((b) => /persona|in your words/i.test(b.innerText)); if (!r) return "no persona row"; r.click(); return "ok"; })()`] },
  { id: "sources", route: "/sources" },
  { id: "claims", route: "/claims" },
  { id: "find-1", route: "/find" },
  { id: "find-2", route: "/find", actions: [`(() => { const o = document.querySelector('main button[aria-pressed], main [role=radio], main [role=option], main button'); if (!o) return "no option"; o.click(); return "ok"; })()`, click("Continue")] },
  { id: "find-review", route: "/find?screen=find-review" },
  { id: "settings", route: "/settings" },
];

const only = process.argv.slice(2);
const wanted = SHOTS.filter((s) => !only.length || only.some((p) => s.id.startsWith(p)));

mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(OUT, ".chrome")}`, "about:blank"], { stdio: "ignore" });

let version;
for (let i = 0; i < 60 && !version; i++) {
  try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); }
}
if (!version) { chrome.kill(); throw new Error("Chrome did not start"); }
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
});
const send = (method, params = {}) => new Promise((r) => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result?.result?.value;

await send("Page.enable");
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });

const index = [];
for (const shot of wanted) {
  for (const size of shot.sizes ?? [PHONE]) {
    for (const theme of THEMES) {
      await send("Emulation.setDeviceMetricsOverride", { width: size.w, height: size.h, deviceScaleFactor: 2, mobile: true });
      const url = `${BASE}${shot.route}${shot.route.includes("?") ? "&" : "?"}mode=${theme}`;
      await send("Page.navigate", { url });
      for (let i = 0; i < 80; i++) {
        await sleep(250);
        if (await evaluate(`document.readyState === "complete" && document.body.innerText.trim().length > 40`)) break;
      }
      await sleep(600);
      const notes = [];
      for (const action of shot.actions ?? []) {
        notes.push(await evaluate(action));
        await sleep(500);
      }
      await sleep(300);
      const facts = await evaluate(`(() => ({
        innerWidth, innerHeight,
        scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight,
        title: document.title, h1: document.querySelector("h1")?.innerText || null,
        primary: [...document.querySelectorAll("a, button")].filter((b) => getComputedStyle(b).backgroundColor === getComputedStyle(document.documentElement).getPropertyValue("--ink").trim() || b.className.includes("bg-ink")).map((b) => b.innerText.trim()).filter(Boolean).slice(0, 4),
        smallText: [...document.querySelectorAll("body *")].filter((e) => e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 11).map((e) => e.textContent.trim().slice(0, 30)).slice(0, 5),
        caps: [...document.querySelectorAll("body *")].filter((e) => [...e.childNodes].some((n) => n.nodeType === 3 && /^[A-Z0-9 .:·]{4,}$/.test(n.textContent.trim()) && /[A-Z]{3}/.test(n.textContent)) && getComputedStyle(e).textTransform !== "uppercase").map((e) => e.textContent.trim().slice(0, 30)).slice(0, 5),
        exclaim: (document.body.innerText.match(/!/g) || []).length,
        demo: /\\bdemo\\b/i.test(document.body.innerText),
        errorOverlay: /Unhandled Runtime Error|Build Error|Runtime Error|Console Error|Failed to compile/.test(document.querySelector("nextjs-portal")?.shadowRoot?.textContent || ""),
      }))()`);
      const name = `${shot.id}-${theme}-${size.w}x${size.h}.png`;
      const png = (await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false })).result?.data;
      if (png) writeFileSync(join(OUT, name), Buffer.from(png, "base64"));
      index.push({ id: shot.id, theme, size: `${size.w}x${size.h}`, file: name, url, actions: notes, ...facts });
      process.stdout.write(`${name} ${facts?.scrollWidth > facts?.innerWidth ? "OVERFLOW " : ""}${facts?.errorOverlay ? "ERROR " : ""}\n`);
    }
  }
}
writeFileSync(join(OUT, "index.json"), JSON.stringify(index, null, 1));
ws.close();
chrome.kill();
console.log(`${index.length} shots in ${OUT}`);
process.exit(0);
