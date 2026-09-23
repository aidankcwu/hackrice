import { describe, expect, it } from "vitest";
import { backendImageUrl, resolveApiBase, sameToken, stripTokenParam, tokenStorageKey, withToken } from "./runtime";

const at = (href: string) => {
  const u = new URL(href);
  return { pathname: u.pathname, origin: u.origin };
};

describe("resolveApiBase", () => {
  it("keeps /t/NAME and drops the /app segment behind the proxy", () => {
    expect(resolveApiBase(at("https://brian.example.com/t/alice/app"))).toBe("https://brian.example.com/t/alice");
    expect(resolveApiBase(at("https://brian.example.com/t/alice/app/protocol/add"))).toBe(
      "https://brian.example.com/t/alice",
    );
  });
  it("is http://localhost:8010 in local dev, as before", () => {
    expect(resolveApiBase(at("http://localhost:3000/"))).toBe("http://localhost:8010");
    expect(resolveApiBase(null)).toBe("http://localhost:8010");
  });
  it("lets NEXT_PUBLIC_API_BASE win", () => {
    expect(resolveApiBase(at("https://x/t/alice/app"), "http://10.0.0.5:8010/")).toBe("http://10.0.0.5:8010");
  });
});

describe("token helpers", () => {
  it("namespaces storage by tester", () => {
    expect(tokenStorageKey("/t/alice/app/x")).toBe("brian.token:/t/alice");
    expect(tokenStorageKey("/t/bob/app")).toBe("brian.token:/t/bob");
    expect(tokenStorageKey("/")).toBe("brian.token");
  });
  it("strips only the token from the address", () => {
    expect(stripTokenParam("https://d/t/a/app/?token=abc&screen=x#h")).toBe("/t/a/app/?screen=x#h");
    expect(stripTokenParam("https://d/t/a/app/")).toBeNull();
  });
  it("puts ?token= on backend image URLs, never on other hosts", () => {
    expect(withToken("/x?a=1#f", "t-_1")).toBe("/x?a=1&token=t-_1#f");
    expect(backendImageUrl("/api/evidence/1/a.jpg", "https://d/t/alice", "tok")).toBe(
      "https://d/t/alice/api/evidence/1/a.jpg?token=tok",
    );
    expect(backendImageUrl("https://evil.example/x.jpg", "https://d/t/alice", "tok")).toBe("https://evil.example/x.jpg");
  });
  it("compares tokens", () => {
    expect(sameToken("abc", "abc")).toBe(true);
    expect(sameToken("abc", "abd")).toBe(false);
    expect(sameToken("abc", "abcd")).toBe(false);
  });
});
