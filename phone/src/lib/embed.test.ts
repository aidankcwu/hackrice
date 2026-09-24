import { describe, expect, it } from "vitest";
import { applyEmbed, EMBED_SCRIPT, type EmbedWindow } from "./embed";

/** A fake window: `search` for the URL, `cookie`, and a session store; `attrs` records `<html>`'s attributes. */
function fakeWindow(search = "", cookie = "", stored: Record<string, string> = {}, storageThrows = false) {
  const attrs = new Map<string, string>();
  const store = { ...stored };
  const guard = () => {
    if (storageThrows) throw new Error("SecurityError");
  };
  const win: EmbedWindow = {
    location: { search },
    document: {
      cookie,
      documentElement: {
        setAttribute: (name, value) => void attrs.set(name, value),
        removeAttribute: (name) => void attrs.delete(name),
      },
    },
    sessionStorage: {
      getItem: (key) => (guard(), store[key] ?? null),
      setItem: (key, value) => void (guard(), (store[key] = value)),
      removeItem: (key) => void (guard(), delete store[key]),
    },
  };
  return { win, attrs, store };
}

describe("applyEmbed", () => {
  it("is off with nothing set", () => {
    const { win, attrs } = fakeWindow();
    expect(applyEmbed(win)).toBe(false);
    expect(attrs.has("data-embed")).toBe(false);
  });

  it("turns on from ?embed=1 and remembers it for the tab", () => {
    const { win, attrs, store } = fakeWindow("?token=T&embed=1");
    expect(applyEmbed(win)).toBe(true);
    expect(attrs.get("data-embed")).toBe("1");
    expect(store["zeroist.embed"]).toBe("1");
  });

  it("stays on after navigating to a URL without the param", () => {
    const first = fakeWindow("?embed=1");
    applyEmbed(first.win);
    const next = fakeWindow("", "", first.store);
    expect(applyEmbed(next.win)).toBe(true);
    expect(next.attrs.get("data-embed")).toBe("1");
  });

  it("honours the zeroist_embed=1 cookie", () => {
    expect(applyEmbed(fakeWindow("", "brian_app_token=x; zeroist_embed=1").win)).toBe(true);
    expect(applyEmbed(fakeWindow("", "zeroist_embed=0").win)).toBe(false);
    expect(applyEmbed(fakeWindow("", "not_zeroist_embed=1").win)).toBe(false);
  });

  it("?embed=0 clears the stored flag and wins over it", () => {
    const { win, attrs, store } = fakeWindow("?embed=0", "", { "zeroist.embed": "1" });
    attrs.set("data-embed", "1");
    expect(applyEmbed(win)).toBe(false);
    expect(attrs.has("data-embed")).toBe(false);
    expect(store["zeroist.embed"]).toBeUndefined();
  });

  it("still applies the URL when storage is blocked", () => {
    expect(applyEmbed(fakeWindow("?embed=1", "", {}, true).win)).toBe(true);
    expect(applyEmbed(fakeWindow("", "zeroist_embed=1", {}, true).win)).toBe(true);
    expect(applyEmbed(fakeWindow("", "", {}, true).win)).toBe(false);
  });
});

describe("EMBED_SCRIPT", () => {
  it("runs on its own, with nothing from the module in scope", () => {
    const { win, attrs } = fakeWindow("?embed=1");
    new Function("window", EMBED_SCRIPT)(win);
    expect(attrs.get("data-embed")).toBe("1");
  });

  it("never throws", () => {
    expect(() => new Function("window", EMBED_SCRIPT)({})).not.toThrow();
  });
});
