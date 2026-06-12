// Sanitization Note: Project reference sanitized
import { defineConfig } from "@trigger.dev/sdk/v3";

export default defineConfig({
  project: "proj_XXXXXXXXXXXX", // Sanitized: real project ID removed
  runtime: "node",
  logLevel: "info",
  retries: {
    enabledInDev: true,
    default: {
      maxAttempts: 3,
      minTimeoutInMs: 1000,
      maxTimeoutInMs: 10000,
      factor: 2,
    },
  },
});
