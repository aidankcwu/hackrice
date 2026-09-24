// Headless Chrome with one page emulating a phone viewport, driven over the
// DevTools protocol. Shared by shot.mjs and overflow.mjs.
//
// Headless Chrome's `--window-size` cannot go below 500 px wide on Windows, so
// `chrome --screenshot --window-size=390,844` lays the page out at 500 px and
// crops it; emulating the viewport over the protocol does not. No dependencies:
// Node 22+ (global WebSocket) and Chrome. `CHROME` overrides the browser path.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const PHONE_URL = process.env.PHONE_URL || "http://localhost:3100";

/** A path or query is resolved against PHONE_URL; a full URL is used as is. */
export const resolveUrl = (target) => (/^[/?]/.test(target) ? new URL(target, PHONE_URL).href : target);

export const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

const CHROME =
  process.env.CHROME ??
  (process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : "google-chrome");

/**
 * Runs `run(page)` against a fresh Chrome and always shuts it down. `page.send`
 * is a raw protocol call, `page.open(url)` navigates and waits for load plus
 * 1.5 s of hydration and the first fetch, `page.evaluate(expr)` returns a value.
 */
export async function withPhone({ width = 390, height = 844 } = {}, run) {
  const port = 9300 + Math.floor(Math.random() * 500);
  const profile = mkdtempSync(join(tmpdir(), "brian-shot-"));
  const chrome = spawn(
    CHROME,
    ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--hide-scrollbars", "--disable-gpu", "--no-first-run", "about:blank"],
    { stdio: "ignore" },
  );
  try {
    const socket = new WebSocket(await pageSocketUrl(port));
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
    await send("Emulation.setDeviceMetricsOverride", { width: Number(width), height: Number(height), deviceScaleFactor: 1, mobile: true });
    const page = {
      send,
      async open(url) {
        const loaded = next("Page.loadEventFired");
        await send("Page.navigate", { url: resolveUrl(url) });
        await loaded;
        await sleep(1500); // hydration and the first fetch
      },
      async evaluate(expression) {
        const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
        if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
        return result.value;
      },
    };
    try {
      return await run(page);
    } finally {
      socket.close();
    }
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
}

async function pageSocketUrl(port) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = targets.find((target) => target.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      // Chrome is still starting.
    }
    await sleep(200);
  }
  throw new Error(`Chrome did not open its debugging port (${CHROME})`);
}
