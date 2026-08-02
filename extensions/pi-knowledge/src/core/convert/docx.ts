import fs from "node:fs/promises";
import mammoth from "mammoth";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";
import { extractMarkdownTitle } from "./text.ts";
import type { ShadowResult } from "./types.ts";

/** docx → mammoth 转 HTML → turndown 转 md（gfm 插件保留表格） */

let turndown: TurndownService | undefined;

function getTurndown(): TurndownService {
  if (!turndown) {
    turndown = new TurndownService({ headingStyle: "atx", codeBlockStyle: "fenced" });
    gfm(turndown);
    // 嵌入图片（data uri）体积大且无检索价值，替换为占位
    turndown.addRule("dropDataUriImages", {
      filter: "img",
      replacement: (_content, node) => {
        const element = node as { getAttribute?: (name: string) => string | null };
        const src = element.getAttribute?.("src") ?? "";
        return src.startsWith("data:") ? "（嵌入图片）" : `![](${src})`;
      },
    });
  }
  return turndown;
}

export async function convertDocx(absPath: string): Promise<ShadowResult> {
  const buffer = await fs.readFile(absPath);
  const result = await mammoth.convertToHtml({ buffer });
  const markdown = getTurndown().turndown(result.value);
  return { markdown, title: extractMarkdownTitle(markdown) };
}
