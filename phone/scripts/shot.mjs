// Screenshot one phone screen at a true 390 x 844 mobile viewport (see chrome.mjs).
//
//   node scripts/shot.mjs "/?screen=today&mode=dark&scale=1" design/shots/today-dark.png
//
// A URL that starts with `/` or `?` is resolved against `PHONE_URL`, the phone
// fixtures dev server (default http://localhost:3100); a full URL is used as is.
// Optional third and fourth arguments: width and height (default 390 844).
// `--scroll-end` scrolls the page to its end before the shot (the last row
// against the tab bar). `--click=<selector>` clicks that element after load (e.g.
// `--click='button[aria-label=Menu]'` to open the menu). `CHROME` overrides the browser path.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { sleep, withPhone } from "./chrome.mjs";

const args = process.argv.slice(2);
const scrollEnd = args.includes("--scroll-end");
const click = args.find((arg) => arg.startsWith("--click="))?.slice("--click=".length);
const [target, out, width = "390", height = "844"] = args.filter((arg) => !arg.startsWith("--"));
if (!target || !out) {
  console.error('usage: node scripts/shot.mjs [--scroll-end] [--click=<selector>] "</path?query | url>" <out.png> [width] [height]');
  process.exit(2);
}

await withPhone({ width, height }, async (page) => {
  await page.open(target);
  if (scrollEnd) {
    await page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)");
    await sleep(500); // the inline title fades in under the bar
  }
  if (click) {
    const found = await page.evaluate(
      `(() => { const el = [...document.querySelectorAll(${JSON.stringify(click)})].find((e) => e.offsetParent !== null); el?.click(); return !!el; })()`,
    );
    if (!found) throw new Error(`nothing visible matches ${click}`);
    await sleep(600); // the menu's 200 ms fade, or a route change
  }
  const { data } = await page.send("Page.captureScreenshot", { format: "png" });
  mkdirSync(dirname(resolve(out)), { recursive: true });
  writeFileSync(resolve(out), Buffer.from(data, "base64"));
  console.log(`${resolve(out)} (${width} x ${height})`);
});
