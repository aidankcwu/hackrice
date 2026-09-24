import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";
import { stripTokenParam, TOKEN_COOKIE } from "./lib/runtime";

// The native app's web tabs open `/calendar?token=T&embed=1` and `/analysis?token=T&embed=1`
// (docs/APP_PRD.md contract): the token must work on any route, not only at `/`.
describe("token handoff on any route", () => {
  beforeEach(() => {
    process.env.ACCESS_TOKEN = "T";
  });
  afterEach(() => {
    delete process.env.ACCESS_TOKEN;
  });

  for (const path of ["/", "/calendar", "/analysis"]) {
    it(`sets the auth cookie from ?token= at ${path}`, () => {
      const res = proxy(new NextRequest(`https://d${path}?token=T&embed=1`));
      expect(res.status).toBe(200);
      expect(res.cookies.get(TOKEN_COOKIE)?.value).toBe("T");
    });
  }

  it("lets the cookie in on a later embedded navigation", () => {
    const req = new NextRequest("https://d/analysis?embed=1", { headers: { cookie: `${TOKEN_COOKIE}=T` } });
    expect(proxy(req).status).toBe(200);
  });

  it("refuses a wrong token", () => {
    expect(proxy(new NextRequest("https://d/calendar?token=nope&embed=1")).status).toBe(401);
  });

  it("strips the token from the address and keeps embed=1", () => {
    expect(stripTokenParam("https://d/calendar?token=T&embed=1")).toBe("/calendar?embed=1");
    expect(stripTokenParam("https://d/t/a/app/analysis?embed=1&token=T")).toBe("/t/a/app/analysis?embed=1");
  });
});
