import fs from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { derivedDir, loadConfig } from "../../config.ts";
import { checkDatabase } from "../../core/db.ts";
import { checkGraph, ensureGraphSchema } from "../../core/graph.ts";
import { runMaintenance } from "../../core/maintain.ts";
import { buildMemoryView, listMemories } from "../../core/memory.ts";
import { markMemoryViewDirty, reflectSessionFile } from "./lifecycle.ts";

/** 把报告送到 UI 或控制台 */
function report(ctx: { hasUI: boolean; ui: { notify(msg: string, level: "info" | "warning"): void } }, text: string, level: "info" | "warning" = "info"): void {
  if (ctx.hasUI) ctx.ui.notify(text, level);
  else console.log(text);
}

/** /knowledge 命令：状态检查等运维入口 */
export function registerKnowledgeCommands(pi: ExtensionAPI): void {
  pi.registerCommand("knowledge", {
    description: "知识库运维：无参数状态检查；maintain 立即跑完整维护（管线+记忆+图谱）",
    getArgumentCompletions: (prefix) =>
      ["maintain"].filter((sub) => sub.startsWith(prefix)).map((sub) => ({ value: sub, label: sub })),
    handler: async (args, ctx) => {
      const config = loadConfig();

      if (args.trim() === "maintain") {
        report(ctx, "维护开始（管线收敛 + 记忆/图谱 agentic 循环），可能需要几分钟...");
        const result = await runMaintenance(config, {
          onProgress: ctx.hasUI ? undefined : (message) => console.log(message),
        });
        markMemoryViewDirty();
        report(
          ctx,
          `维护完成（${Math.round(result.elapsedMs / 1000)}s）：` +
            `vault +${result.vault.ingested}/-${result.vault.removed}，` +
            `session +${result.sessions.ingested}/-${result.sessions.removed}，` +
            `图谱作业 ${result.graph.done}/${result.graph.processed}，` +
            `记忆操作 ${result.memoryLoop.actions.length}，图谱操作 ${result.graphLoop.actions.length}\n报告: ${result.reportPath}`,
        );
        return;
      }

      const lines: string[] = [];

      lines.push(`vault: ${config.vaultDir} ${fs.existsSync(config.vaultDir) ? "✓" : "✗ 不存在"}`);
      lines.push(`derived: ${derivedDir(config)}`);
      lines.push(`ARK key: ${config.ark.apiKey ? "✓ 已配置" : "✗ 缺失（设置 ARK_API_KEY）"}`);

      const dbProblems = await checkDatabase(config);
      lines.push(dbProblems.length === 0 ? "ParadeDB: ✓" : `ParadeDB: ✗ ${dbProblems.join("; ")}`);

      const graphProblems = await checkGraph(config);
      if (graphProblems.length === 0) {
        await ensureGraphSchema(config);
        lines.push("Neo4j: ✓");
      } else {
        lines.push(`Neo4j: ✗ ${graphProblems.join("; ")}`);
      }

      report(ctx, lines.join("\n"), dbProblems.length + graphProblems.length === 0 ? "info" : "warning");
    },
  });

  pi.registerCommand("memory", {
    description: "记忆管理：无参数列出记忆；view 预览开场注入视图；reflect 立即反思本会话",
    getArgumentCompletions: (prefix) =>
      ["view", "reflect"]
        .filter((sub) => sub.startsWith(prefix))
        .map((sub) => ({ value: sub, label: sub })),
    handler: async (args, ctx) => {
      const config = loadConfig();
      const sub = args.trim();

      if (sub === "view") {
        const view = await buildMemoryView(config);
        report(ctx, view === "" ? "暂无记忆，开场不注入。" : view);
        return;
      }

      if (sub === "reflect") {
        const sessionFile = ctx.sessionManager.getSessionFile();
        if (!sessionFile) {
          report(ctx, "当前会话无文件（内存会话），无法反思。", "warning");
          return;
        }
        const saved = await reflectSessionFile(sessionFile);
        report(
          ctx,
          saved.length === 0
            ? "反思完成：未提取到新记忆（会话太短或无增量信息）。"
            : `反思完成，提取 ${saved.length} 条记忆：\n${saved.map((p) => `- ${p}`).join("\n")}`,
        );
        return;
      }

      const memories = await listMemories(config);
      if (memories.length === 0) {
        report(ctx, "暂无记忆。用 memory_save 工具或 /memory reflect 创建。");
        return;
      }
      const lines = memories.map(
        (m) => `- [${m.type}${m.priority === "high" ? "/high" : ""}] ${m.title} (${m.sourcePath})`,
      );
      report(ctx, `共 ${memories.length} 条记忆：\n${lines.join("\n")}`);
    },
  });
}
