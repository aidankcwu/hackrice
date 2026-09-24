import { describe, expect, it } from "vitest";
import { menuItems } from "@/components/menu/Menu";
import { backFallback } from "./screens";

describe("menuItems", () => {
  it("offers My protocol and no Find card in the web app", () => {
    const screens = menuItems(false).map((item) => item.screen);
    expect(screens).toContain("protocol");
    expect(screens).not.toContain("find");
    expect(screens).not.toContain("today");
  });

  it("drops the native tabs and adds Find when embedded", () => {
    const screens = menuItems(true).map((item) => item.screen);
    expect(screens).not.toContain("protocol");
    expect(screens).not.toContain("today");
    expect(screens[0]).toBe("find");
    for (const screen of ["library", "treatments", "tests", "biomarkers", "devices", "concierge", "sources"]) {
      expect(screens).toContain(screen);
    }
  });
});

describe("backFallback", () => {
  it("goes to Today from a menu screen in the web app", () => {
    expect(backFallback("treatments", false)).toBe("/");
    expect(backFallback("protocol", false)).toBe("/protocol");
  });

  it("goes to Analysis, never a native tab, when embedded", () => {
    expect(backFallback("treatments", true)).toBe("/analysis");
    expect(backFallback("find", true)).toBe("/analysis");
    expect(backFallback("protocol", true)).toBe("/analysis");
    expect(backFallback("today", true)).toBe("/analysis");
    expect(backFallback("calendar", true)).toBe("/calendar");
  });
});
