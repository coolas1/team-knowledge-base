import { realpath, readFile } from "node:fs/promises";
import path from "node:path";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const READ_SKILL_PARAMS = Type.Object({
  path: Type.String({
    description: "Path to a packaged TKB SKILL.md or its referenced Markdown file",
  }),
});

function isInside(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function buildSkillReadTool(skillsDir: string): ToolDefinition {
  return defineTool({
    name: "read",
    label: "Read TKB Skill",
    description:
      "Read packaged TKB Agent skill instructions. Access is restricted to the product skills directory.",
    promptSnippet: "read: load a packaged TKB skill when its description matches the task",
    parameters: READ_SKILL_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const root = await realpath(skillsDir);
        const requested = path.isAbsolute(params.path)
          ? params.path
          : path.resolve(root, params.path);
        const target = await realpath(requested);
        if (!isInside(root, target) || path.extname(target).toLowerCase() !== ".md") {
          throw new Error("path is outside the packaged skills directory");
        }
        const content = await readFile(target, "utf8");
        if (Buffer.byteLength(content, "utf8") > 65_536) {
          throw new Error("skill file exceeds 64 KiB");
        }
        return {
          content: [{ type: "text" as const, text: content }],
          details: { path: target },
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          content: [{ type: "text" as const, text: `Unable to read TKB skill: ${message}` }],
          details: { path: params.path },
          isError: true,
        };
      }
    },
  }) as ToolDefinition;
}
