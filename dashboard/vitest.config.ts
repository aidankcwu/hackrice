import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
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
