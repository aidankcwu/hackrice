import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backendUrl,
  isUnderBase,
  resolveApiBase,
  sameToken,
  stripTokenParam,
  tokenStorageKey,
  withBasePath,
  withToken,
  type LocationLike,
} from "./runtime";

const at = (href: string): LocationLike => {
  const u = new URL(href);
  return { protocol: u.protocol, hostname: u.hostname, port: u.port, pathname: u.pathname, origin: u.origin };
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("resolveApiBase", () => {
  it("keeps local dev on :8010 of the same host", () => {
    expect(resolveApiBase(at("http://localhost:3000/"))).toBe("http://localhost:8010");
    expect(resolveApiBase(at("http://192.168.1.20:3000/logs/x"))).toBe("http://192.168.1.20:8010");
  });

  it("uses the same origin and /t/NAME prefix behind the proxy", () => {
    expect(resolveApiBase(at("https://demo.example.com/t/alice/dashboard/"))).toBe("https://demo.example.com/t/alice");
    expect(resolveApiBase(at("https://demo.example.com/t/alice/dashboard/logs/r1"))).toBe("https://demo.example.com/t/alice");
    expect(resolveApiBase(at("https://demo.example.com:8443/t/bob/"))).toBe("https://demo.example.com:8443/t/bob");
    expect(resolveApiBase(at("https://demo.example.com/"))).toBe("https://demo.example.com");
  });

  it("lets NEXT_PUBLIC_API_BASE win outright", () => {
    expect(resolveApiBase(at("https://demo.example.com/t/alice/dashboard/"), "http://10.0.0.5:8010/")).toBe("http://10.0.0.5:8010");
  });
});

describe("token plumbing", () => {
  it("appends the token as a query param, before any hash", () => {
    expect(withToken("/api/evidence/d/f.jpg", "a b")).toBe("/api/evidence/d/f.jpg?token=a%20b");
    expect(withToken("/frames?n=1#x", "t")).toBe("/frames?n=1&token=t#x");
    expect(withToken("/frames", null)).toBe("/frames");
  });

  it("puts server-relative frame paths on the browser's base; absolute URLs pass through", () => {
    expect(backendUrl("/api/evidence/d/f.jpg", "https://d.example/t/alice", "tok")).toBe(
      "https://d.example/t/alice/api/evidence/d/f.jpg?token=tok",
    );
    expect(backendUrl("http://localhost:8010/api/evidence/d/f.jpg", "ignored", null)).toBe("http://localhost:8010/api/evidence/d/f.jpg");
  });

  it("compares tokens exactly", () => {
    expect(sameToken("abc", "abc")).toBe(true);
    expect(sameToken("abc", "abd")).toBe(false);
    expect(sameToken("abc", "abcd")).toBe(false);
    expect(sameToken("", "x")).toBe(false);
  });
});

describe("withBasePath", () => {
  it("leaves paths alone when mounted at the root", () => {
    expect(withBasePath("/api/score")).toBe("/api/score");
    expect(withBasePath("#main")).toBe("#main");
  });
});

describe("backendUrl only hands the token to the backend it derived", () => {
  const base = "https://d.example/t/alice";

  it("tokens absolute URLs under the same origin and /t/NAME prefix", () => {
    expect(backendUrl("https://d.example/t/alice/api/evidence/d/f.jpg", base, "tok")).toBe(
      "https://d.example/t/alice/api/evidence/d/f.jpg?token=tok",
    );
    expect(backendUrl("http://localhost:8010/api/evidence/d/f.jpg", "http://localhost:8010", "tok")).toBe(
      "http://localhost:8010/api/evidence/d/f.jpg?token=tok",
    );
  });

  it("never appends the token to another host, scheme, port, or tester", () => {
    for (const other of [
      "https://evil.example/t/alice/api/evidence/d/f.jpg",
      "http://d.example/t/alice/api/evidence/d/f.jpg",
      "https://d.example:8443/t/alice/api/evidence/d/f.jpg",
      "https://d.example/t/bob/api/evidence/d/f.jpg",
      "https://d.example/t/alicex/api/evidence/d/f.jpg",
      "https://d.example/api/evidence/d/f.jpg",
    ]) {
      expect(backendUrl(other, base, "tok")).toBe(other);
    }
  });

  it("matches on path-segment boundaries", () => {
    expect(isUnderBase("https://d.example/t/alice", base)).toBe(true);
    expect(isUnderBase("https://d.example/t/alice/x", `${base}/`)).toBe(true);
    expect(isUnderBase("https://d.example/t/alicex", base)).toBe(false);
    expect(isUnderBase("https://d.example/anything", "https://d.example")).toBe(true);
    expect(isUnderBase("https://d.example/x", "not a url")).toBe(false);
  });
});

describe("token storage and the address bar", () => {
  it("namespaces the storage key by the /t/NAME mount", () => {
    expect(tokenStorageKey("/t/alice/dashboard/")).toBe("bryan.token:/t/alice");
    expect(tokenStorageKey("/t/bob/dashboard/logs/r1")).toBe("bryan.token:/t/bob");
    expect(tokenStorageKey("/")).toBe("bryan.token");
  });

  it("strips only the token param, keeping other params and the hash", () => {
    expect(stripTokenParam("https://d.example/t/alice/dashboard?token=abc")).toBe("/t/alice/dashboard");
    expect(stripTokenParam("https://d.example/t/alice/dashboard?x=1&token=abc#h")).toBe("/t/alice/dashboard?x=1#h");
    expect(stripTokenParam("https://d.example/t/alice/dashboard?x=1")).toBeNull();
  });

  /** A minimal browser: location + localStorage + history, shared storage across "tabs". */
  function fakeWindow(href: string, storage: Map<string, string>) {
    const loc = new URL(href);
    const replaced: string[] = [];
    const win = {
      location: {
        get href() { return loc.href; },
        get pathname() { return loc.pathname; },
        get search() { return loc.search; },
      },
      localStorage: {
        getItem: (k: string) => storage.get(k) ?? null,
        setItem: (k: string, v: string) => void storage.set(k, v),
      },
      history: {
        state: { __NA: true },
        replaceState: (_s: unknown, _t: string, url: string) => {
          replaced.push(url);
          const next = new URL(url, loc.origin);
          loc.pathname = next.pathname;
          loc.search = next.search;
          loc.hash = next.hash;
        },
      },
    };
    return { win, replaced };
  }

  async function freshRuntime() {
    vi.resetModules();
    return import("./runtime");
  }

  it("keeps Alice's and Bob's tokens apart on one origin", async () => {
    vi.useFakeTimers();
    const storage = new Map<string, string>();

    const alice = fakeWindow("https://d.example/t/alice/dashboard?token=AAA", storage);
    vi.stubGlobal("window", alice.win);
    expect((await freshRuntime()).accessToken()).toBe("AAA");

    const bob = fakeWindow("https://d.example/t/bob/dashboard?token=BBB", storage);
    vi.stubGlobal("window", bob.win);
    expect((await freshRuntime()).accessToken()).toBe("BBB");

    expect(storage.get("bryan.token:/t/alice")).toBe("AAA");
    expect(storage.get("bryan.token:/t/bob")).toBe("BBB");

    // Alice's tab, reloaded later without the query param, still has her own token.
    const aliceAgain = fakeWindow("https://d.example/t/alice/dashboard", storage);
    vi.stubGlobal("window", aliceAgain.win);
    expect((await freshRuntime()).accessToken()).toBe("AAA");
  });

  it("scrubs ?token= from the address bar after capturing it", async () => {
    vi.useFakeTimers();
    const storage = new Map<string, string>();
    const tab = fakeWindow("https://d.example/t/alice/dashboard/logs/r1?view=x&token=AAA#top", storage);
    vi.stubGlobal("window", tab.win);
    const rt = await freshRuntime();

    expect(rt.accessToken()).toBe("AAA");
    expect(tab.replaced).toEqual([]); // deferred: never mid-render
    vi.runAllTimers();
    expect(tab.replaced).toEqual(["/t/alice/dashboard/logs/r1?view=x#top"]);
    expect(tab.win.location.search).toBe("?view=x");
    // After the scrub the token still comes from memory/storage.
    expect(rt.accessToken()).toBe("AAA");
  });

  it("still returns the URL token when storage is blocked", async () => {
    vi.useFakeTimers();
    const tab = fakeWindow("https://d.example/t/alice/dashboard?token=AAA", new Map());
    tab.win.localStorage.setItem = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("window", tab.win);
    const rt = await freshRuntime();
    expect(rt.accessToken()).toBe("AAA");
    vi.runAllTimers();
    expect(rt.accessToken()).toBe("AAA");
  });
});
