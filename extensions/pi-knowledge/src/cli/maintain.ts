import { loadConfig } from "../config.ts";
import { closePool } from "../core/db.ts";
import { closeDriver } from "../core/graph.ts";
import { runMaintenance } from "../core/maintain.ts";

/**
 * pi-kb maintain 独立 CLI 入口（Windows 计划任务驱动，会话触发兜底）：
 *   node --experimental-strip-types src/cli/maintain.ts [--quiet] [--skip-agentic]
 */

const quiet = process.argv.includes("--quiet");
const skipAgentic = process.argv.includes("--skip-agentic");

async function main() {
  const config = loadConfig();
  const report = await runMaintenance(config, {
    skipAgentic,
    onProgress: quiet ? undefined : (message) => console.log(message),
  });
  console.log(
    `维护完成（${Math.round(report.elapsedMs / 1000)}s）：` +
      `vault +${report.vault.ingested}/-${report.vault.removed}，` +
      `session +${report.sessions.ingested}/-${report.sessions.removed}，` +
      `图谱作业 ${report.graph.done}/${report.graph.processed}，` +
      `记忆操作 ${report.memoryLoop.actions.length}，图谱操作 ${report.graphLoop.actions.length}`,
  );
  console.log(`报告: ${report.reportPath}`);
}

main()
  .catch((error) => {
    console.error("维护失败:", error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await Promise.allSettled([closePool(), closeDriver()]);
  });
