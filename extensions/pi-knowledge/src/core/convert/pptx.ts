import fs from "node:fs/promises";
import JSZip from "jszip";
import type { ShadowResult } from "./types.ts";

/** pptx → 解 zip 提取各页 slide XML 里的文本 run（<a:t>），每页一节 */

function decodeXmlEntities(text: string): string {
  return text
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

/** 按段落（<a:p>）聚合文本 run，一个段落一行 */
function extractSlideText(xml: string): string {
  const paragraphs: string[] = [];
  for (const paragraph of xml.split("</a:p>")) {
    const runs = [...paragraph.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => decodeXmlEntities(m[1]));
    const line = runs.join("").trim();
    if (line) paragraphs.push(line);
  }
  return paragraphs.join("\n");
}

export async function convertPptx(absPath: string): Promise<ShadowResult> {
  const buffer = await fs.readFile(absPath);
  const zip = await JSZip.loadAsync(buffer);
  const slideNames = Object.keys(zip.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
    .sort((a, b) => {
      const numA = Number(a.match(/slide(\d+)\.xml$/)?.[1] ?? 0);
      const numB = Number(b.match(/slide(\d+)\.xml$/)?.[1] ?? 0);
      return numA - numB;
    });
  const sections: string[] = [];
  for (const name of slideNames) {
    const xml = await zip.files[name].async("string");
    const text = extractSlideText(xml);
    const page = Number(name.match(/slide(\d+)\.xml$/)?.[1] ?? 0);
    if (text) sections.push(`## 第 ${page} 页\n\n${text}`);
  }
  // 首页第一行通常是演示文稿标题
  const title = sections.length > 0 ? (sections[0].split("\n")[2] ?? "") : "";
  return { markdown: sections.join("\n\n"), title };
}
