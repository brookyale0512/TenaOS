import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted IBM Plex Sans (subset to weights actually used by the design
// system) so the SPA never reaches out to fonts.googleapis.com.
import "@fontsource/ibm-plex-sans/300.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "./index.css";
import App from "./App";
import { loadRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

const root = createRoot(document.getElementById("root")!);

// Apply per-deployment overrides from /runtime-config.json before mounting
// so all consumers of openmrsRuntimeConfig see the merged values.
void loadRuntimeConfig().finally(() => {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
