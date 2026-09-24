import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The `@/` alias from tsconfig.json, so tests can import modules that use it (src/proxy.ts).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
});
