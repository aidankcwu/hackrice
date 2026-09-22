import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // tsconfig says `jsx: preserve` for Next; a test that renders a component
  // needs the automatic runtime, as Next's own compiler uses.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // `server-only` throws outside the Next.js server runtime; tests stub it.
      "server-only": fileURLToPath(new URL("./src/lib/score/server-only.stub.ts", import.meta.url)),
    },
  },
});
