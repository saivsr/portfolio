import { defineConfig } from "@trigger.dev/sdk/v3";

export default defineConfig({
  // Replace with your own Trigger.dev project ref.
  project: "proj_XXXXXXXXXXXXXXXX",
  runtime: "node",
  logLevel: "info",
  maxDuration: 120,
  dirs: ["./src/trigger"],
});
