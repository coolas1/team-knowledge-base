import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { formatContractErrors, validateEngineContract } from "./contract.js";
import { loadTkbAdapterConfig } from "./config.js";
import { TkbMcpClient } from "./mcp-client.js";
import { registerTkbTools } from "./tools.js";

export default function tkbPiAgentAdapter(pi: ExtensionAPI): void {
  const config = loadTkbAdapterConfig();
  const client = new TkbMcpClient(config);
  registerTkbTools(pi, { client, config });

  pi.on("session_start", async (_event, ctx) => {
    try {
      const report = await validateEngineContract(client);
      if (!report.ok) {
        const message = `TKB MCP contract mismatch: ${formatContractErrors(report)}`;
        if (config.strictContract) throw new Error(message);
        ctx.ui.notify(message, "error");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.ui.notify(`TKB MCP validation failed: ${message}`, "error");
      if (config.strictContract) throw error;
    }
  });
}
