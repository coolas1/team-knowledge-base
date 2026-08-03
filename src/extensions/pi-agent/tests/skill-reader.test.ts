import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { buildSkillReadTool } from "../src/skill-reader.js";

const cleanup: string[] = [];

afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((item) => rm(item, { recursive: true, force: true })));
});

describe("restricted skill reader", () => {
  it("reads Markdown inside the skills directory", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "tkb-skills-"));
    cleanup.push(root);
    await mkdir(path.join(root, "search"));
    await writeFile(path.join(root, "search", "SKILL.md"), "# Search", "utf8");
    const tool = buildSkillReadTool(root);

    const result = await tool.execute(
      "1",
      { path: "search/SKILL.md" },
      undefined,
      undefined,
      {} as never,
    );
    expect(result.content[0]).toMatchObject({ text: "# Search" });
  });

  it("rejects paths outside the skills directory", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "tkb-skills-"));
    cleanup.push(root);
    const outside = path.join(root, "..", "outside.md");
    await writeFile(outside, "secret", "utf8");
    const tool = buildSkillReadTool(root);

    const result = await tool.execute(
      "1",
      { path: outside },
      undefined,
      undefined,
      {} as never,
    );
    expect((result as { isError?: boolean }).isError).toBe(true);
    await rm(outside, { force: true });
  });
});
