import path from "path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      // Scope coverage to the critical-path business logic. Pure UI shells
      // (Card, Sidebar, Workspace, login screen, route guards, etc.) are
      // exercised through Playwright smoke tests later; gating on them in
      // unit coverage encourages brittle "render-and-snapshot" tests
      // instead of behavioural ones.
      include: [
        "src/lib/api/errors.ts",
        "src/lib/openmrs/runtimeConfig.ts",
        "src/lib/openmrs/idgen.ts",
        "src/lib/hooks/useDebouncedValue.ts",
        "src/lib/utils.ts",
        "src/features/auth/useSession.ts",
        "src/features/visits/utils/visitStatus.ts",
        "src/features/clinical/utils/importedObservations.ts",
      ],
      exclude: [
        "src/**/*.{test,spec}.{ts,tsx}",
        "src/test/**",
        "src/types/**",
        "src/**/index.ts",
      ],
      thresholds: {
        statements: 70,
        branches: 70,
        functions: 70,
        lines: 70,
      },
    },
  },
});
