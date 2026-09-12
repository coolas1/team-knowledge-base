import { describe, expect, it, vi } from "vitest";
import type { TkbMcpClient } from "../src/mcp-client.js";
import { buildAllTkbTools } from "../src/tools.js";

describe("chat-native image PPT tool", () => {
  it("uses one foreground MCP call and exposes the artifact identity", async () => {
    const client = {
      callTool: vi.fn(async () => ({
        text: JSON.stringify({
          id: "9b8a4870-fb11-43f8-a9c3-0a4f1391ac58",
          download_url: "/api/artifacts/9b8a4870-fb11-43f8-a9c3-0a4f1391ac58/download",
        }),
        isError: false,
      })),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({ client }).find(
      candidate => candidate.name === "tkb_generate_image_ppt",
    )!;
    const spec = {
      title: "路线图",
      style: "简洁",
      pages: [{ title: "目标", points: ["发布"], layout: "封面", notes: "介绍目标" }],
    };

    const result = await tool.execute(
      "call",
      { spec, file_name: "roadmap" },
      undefined,
      undefined,
      {} as never,
    );

    expect(client.callTool).toHaveBeenCalledTimes(1);
    expect(client.callTool).toHaveBeenCalledWith(
      "generate_image_ppt",
      { spec, file_name: "roadmap" },
      expect.any(Object),
    );
    expect(result.details).toMatchObject({
      activity: "ppt",
      artifactId: "9b8a4870-fb11-43f8-a9c3-0a4f1391ac58",
    });
  });

  it("allows only one PPT invocation per chat turn", async () => {
    const client = {
      callTool: vi.fn(async () => ({ text: "{}", isError: false })),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({ client }).find(
      candidate => candidate.name === "tkb_generate_image_ppt",
    )!;
    const params = {
      spec: {
        title: "路线图",
        style: "简洁",
        pages: [{ title: "目标", points: ["发布"], layout: "封面", notes: "介绍目标" }],
      },
    };

    await tool.execute("first", params, undefined, undefined, {} as never);
    const repeated = await tool.execute("second", params, undefined, undefined, {} as never);

    expect(client.callTool).toHaveBeenCalledOnce();
    expect(repeated).toMatchObject({ isError: true, terminate: true });
  });

  it("returns a visible terminal result when generation fails", async () => {
    const client = {
      callTool: vi.fn(async () => ({
        text: JSON.stringify({ error: "visual_check_failed", page: 1 }),
        isError: true,
      })),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({ client }).find(
      candidate => candidate.name === "tkb_generate_image_ppt",
    )!;

    const result = await tool.execute(
      "failed",
      {
        spec: {
          title: "路线图",
          style: "简洁",
          pages: [{ title: "目标", points: ["发布"], layout: "封面", notes: "介绍目标" }],
        },
      },
      undefined,
      undefined,
      {} as never,
    );

    expect(result).toMatchObject({
      isError: false,
      details: { activity: "ppt", errorSummary: expect.stringContaining("visual_check_failed") },
    });
    expect(result.content[0]).toMatchObject({ text: expect.stringContaining("Do not call") });
  });
});
