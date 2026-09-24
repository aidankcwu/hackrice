// Screenshot one phone screen at a true 390 x 844 mobile viewport.
//
// Headless Chrome's `--window-size` cannot go below 500 px wide on Windows, so
// `chrome --screenshot --window-size=390,844` lays the page out at 500 px and
// crops it. This drives Chrome over the DevTools protocol instead and emulates
// the phone's viewport. No dependencies: Node 22+ (global WebSocket) and Chrome.
//
//   node scripts/shot.mjs "/?screen=today&mode=dark&scale=1" design/shots/today-dark.png
//
// A URL that starts with `/` or `?` is resolved against `PHONE_URL`, the phone
// fixtures dev server (default http://localhost:3100); a full URL is used as is.
// Optional third and fourth arguments: width and height (default 390 844).
// `--scroll-end` scrolls the page to its end before the shot (the last row
// against the tab bar). `--click=<selector>` clicks that element after load (e.g.
// `--click='button[aria-label=Menu]'` to open the menu). `CHROME` overrides the browser path.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

const args = process.argv.slice(2);
const scrollEnd = args.includes("--scroll-end");
const click = args.find((arg) => arg.startsWith("--click="))?.slice("--click=".length);
const [target, out, width = "390", height = "844"] = args.filter((arg) => !arg.startsWith("--"));
if (!target || !out) {
  console.error('usage: node scripts/shot.mjs [--scroll-end] [--click=<selector>] "</path?query | url>" <out.png> [width] [height]');
  process.exit(2);
}
const PHONE_URL = process.env.PHONE_URL || "http://localhost:3100";
const url = /^[/?]/.test(target) ? new URL(target, PHONE_URL).href : target;

const CHROME =
  process.env.CHROME ??
  (process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : "google-chrome");
const PORT = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "brian-shot-"));
const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    "--hide-scrollbars",
    "--disable-gpu",
    "--no-first-run",
    "about:blank",
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function pageSocketUrl() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = targets.find((target) => target.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      // Chrome is still starting.
    }
    await sleep(200);
  }
  throw new Error(`Chrome did not open its debugging port (${CHROME})`);
}

try {
  const socket = new WebSocket(await pageSocketUrl());
  await new Promise((open, fail) => {
    socket.onopen = open;
    socket.onerror = fail;
  });
  let nextId = 0;
  const replies = new Map();
  const listeners = new Map();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) replies.get(message.id)?.(message);
    else listeners.get(message.method)?.(message.params);
  };
  const send = (method, params = {}) =>
    new Promise((done, fail) => {
      const id = (nextId += 1);
      replies.set(id, (message) => (message.error ? fail(new Error(message.error.message)) : done(message.result)));
      socket.send(JSON.stringify({ id, method, params }));
    });
  const next = (method) => new Promise((done) => listeners.set(method, done));

  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: Number(width),
    height: Number(height),
    deviceScaleFactor: 1,
    mobile: true,
  });
  const loaded = next("Page.loadEventFired");
  await send("Page.navigate", { url });
  await loaded;
  await sleep(1500); // hydration and the first fetch
  if (scrollEnd) {
    await send("Runtime.evaluate", {
      expression: "window.scrollTo(0, document.scrollingElement.scrollHeight)",
    });
    await sleep(500); // the inline title fades in under the bar
  }
  if (click) {
    const { result } = await send("Runtime.evaluate", {
      expression: `(() => { const el = [...document.querySelectorAll(${JSON.stringify(click)})].find((e) => e.offsetParent !== null); el?.click(); return !!el; })()`,
      returnByValue: true,
    });
    if (!result.value) throw new Error(`nothing visible matches ${click}`);
    await sleep(600); // the menu's 200 ms fade, or a route change
  }
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  mkdirSync(dirname(resolve(out)), { recursive: true });
  writeFileSync(resolve(out), Buffer.from(data, "base64"));
  console.log(`${resolve(out)} (${width} x ${height})`);
  socket.close();
} finally {
  const exited = new Promise((done) => chrome.once("exit", done));
  chrome.kill();
  await Promise.race([exited, sleep(3000)]);
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } catch {
    // A Chrome helper process still holds a file; the OS clears its temp dir.
  }
}
