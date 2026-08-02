import ExcelJS from "exceljs";
import { rowsToMarkdownTable } from "./text.ts";
import type { ShadowResult } from "./types.ts";

/** xlsx → 每个工作表一节 md 表格 */

const MAX_ROWS_PER_SHEET = 1000;

function cellText(value: ExcelJS.CellValue): string {
  if (value === null || value === undefined) return "";
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value === "object") {
    if ("richText" in value) return value.richText.map((part) => part.text).join("");
    if ("text" in value) return String(value.text);
    if ("result" in value) return cellText(value.result as ExcelJS.CellValue);
    if ("error" in value) return String(value.error);
    return "";
  }
  return String(value);
}

export async function convertXlsx(absPath: string): Promise<ShadowResult> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.readFile(absPath);
  const sections: string[] = [];
  workbook.eachSheet((sheet) => {
    const rows: string[][] = [];
    sheet.eachRow((row) => {
      const cells: string[] = [];
      // row.values 是 1 基数组，跳过第 0 位
      row.eachCell({ includeEmpty: true }, (cell) => {
        cells.push(cellText(cell.value));
      });
      rows.push(cells);
    });
    if (rows.length === 0) return;
    sections.push(`## 工作表：${sheet.name}\n\n${rowsToMarkdownTable(rows, MAX_ROWS_PER_SHEET)}`);
  });
  return { markdown: sections.join("\n\n"), title: "" };
}
