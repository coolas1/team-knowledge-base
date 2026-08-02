import fs from "node:fs/promises";
import path from "node:path";
import { arkChat } from "../ark.ts";
import type { KnowledgeConfig } from "../../config.ts";
import type { ShadowResult } from "./types.ts";

/** 图片 → VLM 生成检索友好的文字描述，chunk 通过 assetPath 回指原图 */

const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".bmp": "image/bmp",
};

export function imageMime(ext: string): string | undefined {
  return IMAGE_MIME[ext.toLowerCase()];
}

const DESCRIBE_PROMPT = `请描述这张图片，用于知识库检索。要求：
1. 第一行用一句话概括图片内容
2. 之后详细描述：主体对象、文字内容（如有则逐字转写）、图表数据（如有则转写为表格）、场景与布局
3. 只输出描述本身，不要客套语`;

export async function convertImage(
  config: KnowledgeConfig,
  absPath: string,
  relPath: string,
): Promise<ShadowResult> {
  const mime = imageMime(path.extname(absPath));
  if (!mime) throw new Error(`不支持的图片格式: ${absPath}`);
  const buffer = await fs.readFile(absPath);
  const dataUrl = `data:${mime};base64,${buffer.toString("base64")}`;
  let description: string;
  try {
    description = await arkChat(config, config.ark.visionModel, [
      {
        role: "user",
        content: [
          { type: "image_url", image_url: { url: dataUrl } },
          { type: "text", text: DESCRIBE_PROMPT },
        ],
      },
    ]);
  } catch (error) {
    // 过小/损坏的图片 VLM 拒收，降级为仅文件名占位
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("InvalidParameter") || message.includes("BadRequest")) {
      description = `（图片无法解析：${path.basename(absPath)}）`;
    } else {
      throw error;
    }
  }
  const title = description.split("\n")[0]?.trim() ?? "";
  return {
    markdown: `![${title}](${relPath.replaceAll("\\", "/")})\n\n${description}`,
    title,
    assetPath: relPath,
  };
}
