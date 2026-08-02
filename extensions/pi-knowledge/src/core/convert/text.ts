import fs from "node:fs/promises";
import path from "node:path";
import type { ShadowResult } from "./types.ts";

/** 直通类格式：md / txt / 代码 / csv → 影子 md */

/** 代码文件扩展名 → 围栏语言标记 */
const CODE_LANGUAGES: Record<string, string> = {
  ".ts": "typescript",
  ".tsx": "typescript",
  ".js": "javascript",
  ".jsx": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".py": "python",
  ".java": "java",
  ".go": "go",
  ".rs": "rust",
  ".c": "c",
  ".h": "c",
  ".cpp": "cpp",
  ".hpp": "cpp",
  ".cs": "csharp",
  ".rb": "ruby",
  ".php": "php",
  ".sh": "bash",
  ".ps1": "powershell",
  ".sql": "sql",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".toml": "toml",
  ".json": "json",
  ".xml": "xml",
  ".html": "html",
  ".css": "css",
};

export function codeLanguage(ext: string): string | undefined {
  return CODE_LANGUAGES[ext.toLowerCase()];
}

/** 从 markdown 正文提取第一个一级/二级标题 */
export function extractMarkdownTitle(markdown: string): string {
  const match = markdown.match(/^#{1,2}\s+(.+)$/m);
  return match ? match[1].trim() : "";
}

export async function convertMarkdown(absPath: string): Promise<ShadowResult> {
  const markdown = await fs.readFile(absPath, "utf8");
  return { markdown, title: extractMarkdownTitle(markdown) };
}

export async function convertPlainText(absPath: string): Promise<ShadowResult> {
  const markdown = await fs.readFile(absPath, "utf8");
  return { markdown, title: "" };
}

export async function convertCode(absPath: string): Promise<ShadowResult> {
  const source = await fs.readFile(absPath, "utf8");
  const lang = codeLanguage(path.extname(absPath)) ?? "";
  return {
    markdown: `\`\`\`${lang}\n${source}\n\`\`\``,
    title: "",
  };
}

/** 最小 CSV 解析：支持双引号包裹、内嵌逗号/换行/转义引号 */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ""));
}

function escapeCell(cell: string): string {
  return cell.replaceAll("|", "\\|").replaceAll(/\r?\n/g, " ").trim();
}

export function rowsToMarkdownTable(rows: string[][], maxRows: number): string {
  if (rows.length === 0) return "";
  const width = Math.max(...rows.map((r) => r.length));
  const pad = (r: string[]) => [...r, ...Array(width - r.length).fill("")];
  const lines: string[] = [];
  lines.push(`| ${pad(rows[0]).map(escapeCell).join(" | ")} |`);
  lines.push(`| ${Array(width).fill("---").join(" | ")} |`);
  const body = rows.slice(1, 1 + maxRows);
  for (const row of body) {
    lines.push(`| ${pad(row).map(escapeCell).join(" | ")} |`);
  }
  if (rows.length - 1 > maxRows) {
    lines.push("");
    lines.push(`（共 ${rows.length - 1} 行数据，此处截取前 ${maxRows} 行）`);
  }
  return lines.join("\n");
}

export async function convertCsv(absPath: string): Promise<ShadowResult> {
  const text = await fs.readFile(absPath, "utf8");
  const rows = parseCsv(text);
  return { markdown: rowsToMarkdownTable(rows, 2000), title: "" };
}
