import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { getDocument, GlobalWorkerOptions } from "pdfjs-dist/legacy/build/pdf.mjs";
import type { ShadowResult } from "./types.ts";

/**
 * 使用 pdfjs-dist/legacy 构建 + useWorkerFetch:false，
 * 使 CMap/StandardFont 加载走主线程 NodeBinaryDataFactory（fs.readFile 支持 file://）。
 * unpdf 默认 serverless 构建的 worker fetch 不支持 file:// 协议。
 */
const require = createRequire(import.meta.url);
const pdfjsDistDir = path.dirname(require.resolve("pdfjs-dist/package.json"));
// pdfjs 内部 NodeBinaryDataFactory 用 fs.readFile(url) 读取，
// Node fs 只接受普通路径字符串或 URL 对象，不接受 "file://" 格式字符串。
// pdfjs 还要求 trailing slash（正斜杠），Windows path.sep 是反斜杠需替换。
const CMAP_URL = path.join(pdfjsDistDir, "cmaps").replaceAll("\\", "/") + "/";
const STANDARD_FONT_URL = path.join(pdfjsDistDir, "standard_fonts").replaceAll("\\", "/") + "/";

GlobalWorkerOptions.workerSrc = pathToFileURL(
  path.join(pdfjsDistDir, "legacy", "build", "pdf.worker.mjs"),
).href;

/** 文本型 pdf → 每页一节 md（扫描件无文本层，产出为空时由调用方跳过） */
export async function convertPdf(absPath: string): Promise<ShadowResult> {
  const buffer = await fs.readFile(absPath);
  const doc = await getDocument({
    data: new Uint8Array(buffer),
    cMapUrl: CMAP_URL,
    cMapPacked: true,
    standardFontDataUrl: STANDARD_FONT_URL,
    useSystemFonts: true,
    isEvalSupported: false,
    useWorkerFetch: false,
  } as Parameters<typeof getDocument>[0]).promise;

  const sections: string[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const pageText = content.items
      .map((item) => ("str" in item ? item.str : ""))
      .join("")
      .trim();
    if (pageText) sections.push(`## 第 ${i} 页\n\n${pageText}`);
  }
  await doc.cleanup();
  return { markdown: sections.join("\n\n"), title: "" };
}
