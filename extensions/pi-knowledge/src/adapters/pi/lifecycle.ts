import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadConfig } from "../../config.ts";
import { readMaintenanceState, writeMaintenanceState } from "../../core/maintain.ts";
import { buildMemoryView, reflectAndSave } from "../../core/memory.ts";
import { extractSessionText, sessionDerivedFrom } from "../../core/session-ingest.ts";

/**
 * 生命周期钩子：
 * - 开场注入：每次 agent 启动前把记忆压缩视图追加到系统提示（视图缓存，记忆变更后重建）
 * - 结束反思：会话结束（quit/new/resume/fork）时 LLM 反思提取记忆直接入库
 * - 维护兜底：会话结束时若维护超期，detached 拉起 maintain CLI（不阻塞退出）
 */

let cachedView = "";
let viewDirty = true;

/** 记忆有变更（保存/反思/手工编辑后重摄取）时调用，下次注入前重建视图 */
export function markMemoryViewDirty(): void {
  viewDirty = true;
}

const REFLECT_TIMEOUT_MS = 45_000;
/** 短会话（寒暄、单条命令）不值得反思 */
const MIN_CONVERSATION_CHARS = 200;
/** 维护超过此时长未跑时，会话结束兜底触发 */
const MAINTAIN_STALE_MS = 7 * 24 * 3600_000;
/** 触发冷却：避免连续多个会话退出重复拉起 */
const TRIGGER_COOLDOWN_MS = 24 * 3600_000;

/** 维护超期时 detached 拉起 maintain CLI（兜底，主路径是 Windows 计划任务） */
async function maybeTriggerMaintenance(): Promise<void> {
  const config = loadConfig();
  if (!config.ark.apiKey) return;
  const state = await readMaintenanceState(config);
  const now = Date.now();
  const lastRun = state.lastRun ? Date.parse(state.lastRun) : 0;
  const lastTriggered = state.lastTriggered ? Date.parse(state.lastTriggered) : 0;
  if (now - lastRun < MAINTAIN_STALE_MS || now - lastTriggered < TRIGGER_COOLDOWN_MS) return;
  await writeMaintenanceState(config, { lastTriggered: new Date(now).toISOString() });
  const cliPath = fileURLToPath(new URL("../../cli/maintain.ts", import.meta.url));
  spawn(process.execPath, ["--experimental-strip-types", cliPath, "--quiet"], {
    detached: true,
    stdio: "ignore",
  }).unref();
}

/** 读会话文件 → 反思提取记忆，返回保存的相对路径列表 */
export async function reflectSessionFile(sessionFile: string): Promise<string[]> {
  const raw = await fs.readFile(sessionFile, "utf8");
  const extracted = extractSessionText(raw);
  if (!extracted || extracted.markdown.length < MIN_CONVERSATION_CHARS) return [];
  const saved = await reflectAndSave(
    loadConfig(),
    extracted.markdown,
    sessionDerivedFrom(sessionFile),
  );
  if (saved.length > 0) markMemoryViewDirty();
  return saved;
}

export function registerKnowledgeLifecycle(pi: ExtensionAPI): void {
  pi.on("session_start", () => {
    viewDirty = true;
  });

  pi.on("before_agent_start", async (event) => {
    if (viewDirty) {
      try {
        cachedView = await buildMemoryView(loadConfig());
        viewDirty = false;
      } catch {
        return; // 数据库不可用等：本轮跳过注入，不阻塞对话
      }
    }
    if (cachedView === "") return;
    return {
      systemPrompt: `${event.systemPrompt}\n\n以下是跨会话长期记忆（高优先级为全文，其余为一行索引，可用知识库检索工具按路径深入）：\n\n${cachedView}`,
    };
  });

  pi.on("session_shutdown", async (event, ctx) => {
    if (event.reason === "reload") return; // 会话未结束，稍后还会反思
    maybeTriggerMaintenance().catch(() => {}); // 兜底维护，失败无声
    const sessionFile = ctx.sessionManager.getSessionFile();
    if (!sessionFile) return;
    let timer: NodeJS.Timeout | undefined;
    try {
      const timeout = new Promise<string[]>((resolve) => {
        timer = setTimeout(() => resolve([]), REFLECT_TIMEOUT_MS);
      });
      const saved = await Promise.race([reflectSessionFile(sessionFile), timeout]);
      if (saved.length > 0 && ctx.hasUI && event.reason !== "quit") {
        ctx.ui.notify(`会话反思：已提取 ${saved.length} 条记忆`, "info");
      }
    } catch {
      // 反思失败（ARK 不可达等）不阻塞退出
    } finally {
      clearTimeout(timer);
    }
  });
}
