import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ShellFrame } from "./Shell";

vi.mock("next/navigation", () => ({ useRouter: () => ({ back() {}, push() {} }) }));

const render = (screen: "analysis" | "calendar", embedded: boolean) =>
  renderToStaticMarkup(
    <ShellFrame screen={screen} embedded={embedded}>
      <p>content</p>
    </ShellFrame>,
  );

describe("Shell in embed mode", () => {
  for (const screen of ["analysis", "calendar"] as const) {
    it(`${screen}: no hamburger, no title, no Find pill when embedded`, () => {
      const html = render(screen, true);
      expect(html).not.toContain('aria-label="Menu"');
      expect(html).not.toContain("<h1");
      expect(html).not.toContain("Find my protocol");
      expect(html).not.toContain('href="/find"');
      expect(html).toContain("content");
    });

    it(`${screen}: all three when not embedded`, () => {
      const html = render(screen, false);
      expect(html).toContain('aria-label="Menu"');
      expect(html).toContain("<h1");
      expect(html).toContain("Find my protocol");
    });
  }

  it("a pushed screen keeps its back button when embedded", () => {
    const html = renderToStaticMarkup(<ShellFrame screen="treatments" embedded />);
    expect(html).toContain('aria-label="Back"');
  });
});
