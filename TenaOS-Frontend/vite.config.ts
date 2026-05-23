import path from "path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * The Vite dev server proxies /openmrs to the local OpenMRS instance so the
 * frontend can talk to the REST API without CORS. By default the browser
 * sends its own cookies (`JSESSIONID`) and the user authenticates through
 * the in-app login page.
 *
 * For exploratory work against an OpenMRS that has no login UI configured,
 * developers can opt into the legacy "inject admin Basic auth" behavior by
 * setting OPENMRS_DEV_INJECT_BASIC=true in their local .env. This is OFF by
 * default and is never used in production builds.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const openmrsTarget = env.OPENMRS_PROXY_TARGET || "http://localhost:18080";
  const tenaAgentTarget = env.TENA_AGENT_PROXY_TARGET || env.CDS_PROXY_TARGET || "http://localhost:8095";
  const injectBasic = env.OPENMRS_DEV_INJECT_BASIC === "true";
  const proxyUser = env.OPENMRS_DEV_USERNAME || "admin";
  const proxyPassword = env.OPENMRS_DEV_PASSWORD || "Admin123";
  const devBasicAuth = injectBasic
    ? `Basic ${Buffer.from(`${proxyUser}:${proxyPassword}`).toString("base64")}`
    : undefined;

  return {
    plugins: [tailwindcss(), react()],
    build: {
      sourcemap: false,
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/openmrs": {
          target: openmrsTarget,
          changeOrigin: true,
          // Forward Set-Cookie so the browser receives JSESSIONID after login.
          cookieDomainRewrite: { "*": "" },
          configure: (proxy) => {
            if (devBasicAuth) {
              proxy.on("proxyReq", (proxyReq) => {
                if (!proxyReq.getHeader("authorization")) {
                  proxyReq.setHeader("Authorization", devBasicAuth);
                }
              });
            }
          },
        },
        "/agent-api": {
          target: tenaAgentTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/agent-api/, ""),
        },
        // Temporary compatibility alias for older local envs/bookmarks.
        "/cds-api": {
          target: tenaAgentTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/cds-api/, ""),
        },
      },
    },
  };
});
