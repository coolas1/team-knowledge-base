import path from "node:path";
import type { KnowledgeConfig } from "../../config.ts";
import { convertDocx } from "./docx.ts";
import { convertImage, imageMime } from "./image.ts";
import { convertPdf } from "./pdf.ts";
import { convertPptx } from "./pptx.ts";
import { codeLanguage, convertCode, convertCsv, convertMarkdown, convertPlainText } from "./text.ts";
import { convertXlsx } from "./xlsx.ts";
import type { ShadowResult } from "./types.ts";

export type { ShadowResult } from "./types.ts";

/** 统一分发：源文件 → 影子 md。不支持的格式返回 undefined，由摄取管线跳过 */
export async function convertToShadow(
  config: KnowledgeConfig,
  absPath: string,
  relPath: string,
): Promise<ShadowResult | undefined> {
  const ext = path.extname(absPath).toLowerCase();
  switch (ext) {
    case ".md":
    case ".markdown":
      return convertMarkdown(absPath);
    case ".txt":
      return convertPlainText(absPath);
    case ".csv":
      return convertCsv(absPath);
    case ".docx":
      return convertDocx(absPath);
    case ".xlsx":
      return convertXlsx(absPath);
    case ".pptx":
      return convertPptx(absPath);
    case ".pdf":
      return convertPdf(absPath);
    default:
      if (imageMime(ext)) return convertImage(config, absPath, relPath);
      if (codeLanguage(ext)) return convertCode(absPath);
      return undefined;
  }
}

export function isSupported(filePath: string): boolean {
  const ext = path.extname(filePath).toLowerCase();
  return (
    [".md", ".markdown", ".txt", ".csv", ".docx", ".xlsx", ".pptx", ".pdf"].includes(ext) ||
    imageMime(ext) !== undefined ||
    codeLanguage(ext) !== undefined
  );
}
