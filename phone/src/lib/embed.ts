/**
 * Embed mode: the native Zeroist app shows Calendar and Analysis in a web view
 * (docs/APP_PRD.md, "Contract between native and web"). It opens
 * `/analysis?embed=1` (and `/calendar`); the page then drops its tab bar, top pill,
 * hamburger and title (Shell.tsx), and links nowhere else (embedReach.test.tsx).
 *
 * Embed is on when any of these says so, checked in this order:
 *
 *   ?embed=1 / ?embed=0        the URL; stored for the rest of the tab (0 clears it)
 *   sessionStorage             so a reload or an in-app link without the param keeps it
 *   zeroist_embed=1 cookie     set by the native app on its web view's cookie store
 *
 * The result is the `data-embed` attribute on `<html>`, set by an inline script
 * in the root layout before first paint (no flash of the web tab bar) and again
 * by `EmbedCapture` after hydration (React's dev remount clears it). CSS reads
 * it through the `embed:` variant (globals.css); code reads `isEmbedded()`.
 */

export const EMBED_PARAM = "embed";
export const EMBED_COOKIE = "zeroist_embed";
export const EMBED_STORAGE_KEY = "zeroist.embed";
export const EMBED_ATTR = "data-embed";

/** The parts of `window` that `applyEmbed` touches, so tests can pass a fake. */
export interface EmbedWindow {
  location: { search: string };
  document: { cookie: string; documentElement: { setAttribute(name: string, value: string): void; removeAttribute(name: string): void } };
  sessionStorage: { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void };
}

/**
 * Decides embed mode for `win`, persists it, and sets or clears `data-embed`.
 * Returns the decision.
 *
 * Self-contained on purpose: the root layout inlines `applyEmbed.toString()` as
 * a blocking script, so this function may not reference anything outside
 * itself (not even the constants above; they are repeated inside).
 */
export function applyEmbed(win: EmbedWindow): boolean {
  const param = "embed";
  const key = "zeroist.embed";
  const cookie = "zeroist_embed";
  let on = false;
  let fromUrl: string | null = null;
  try {
    fromUrl = new URLSearchParams(win.location.search).get(param);
  } catch {
    fromUrl = null;
  }
  if (fromUrl === "1" || fromUrl === "0") {
    on = fromUrl === "1";
    try {
      if (on) win.sessionStorage.setItem(key, "1");
      else win.sessionStorage.removeItem(key);
    } catch {
      // Storage blocked: the attribute still carries this page load.
    }
  } else {
    try {
      on = win.sessionStorage.getItem(key) === "1";
    } catch {
      on = false;
    }
    if (!on) {
      on = (win.document.cookie || "").split(";").some((part) => part.trim() === cookie + "=1");
    }
  }
  if (on) win.document.documentElement.setAttribute("data-embed", "1");
  else win.document.documentElement.removeAttribute("data-embed");
  return on;
}

/** The blocking `<head>` script: `applyEmbed(window)`, never throwing. */
export const EMBED_SCRIPT = `try{(${applyEmbed.toString()})(window)}catch(e){}`;

/** True when this page is embedded in the native app. False during prerender. */
export function isEmbedded(): boolean {
  return typeof document !== "undefined" && document.documentElement.hasAttribute(EMBED_ATTR);
}
