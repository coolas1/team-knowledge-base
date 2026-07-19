"""表格提取器：将 CSV / XLSX 转为结构化键值对文本。

设计要点（针对 RAG 检索优化）：
- 每个表格首先生成一个「摘要块」，包含文件名、列说明、数字列聚合统计
  （总和/最大/最小/行数），弥合"聚合查询"与"逐行明细"之间的语义鸿沟。
  例如 Q29 问"旅行总花费"时，摘要块中的"amount_jpy 总和=120200"可直接命中。
- 每行转为 "列名=值 | 列名=值" 的结构化记录，让向量检索能命中具体数值查询
  （如 "B-013 的温度/pH"、"最高 SST"、"最高平均评分"）。
- 每隔 N 行重复一次列头，保证分块后每个 chunk 都自带列语义上下文，
  避免后面的 chunk 丢失表头导致 LLM 无法理解各列含义。
- XLSX 多 sheet 逐个展开，sheet 名作为上下文标题。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.pipeline.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

# 每隔多少行重复一次列头（保证 chunk 自含列上下文）
HEADER_REPEAT_ROWS = 12


def _fmt_value(v) -> str:
    """格式化单元格值，处理 NaN 和浮点数。"""
    if pd.isna(v):
        return ""
    if isinstance(v, float):
        # 整数值的浮点去掉 .0
        if v == int(v):
            return str(int(v))
        return f"{v:g}"
    return str(v).strip()


def _generate_summary(df: pd.DataFrame, sheet_label: str) -> str:
    """为表格生成摘要块：文件名 + 列说明 + 数字列聚合统计。

    解决「聚合语义鸿沟」：当查询问"总额/总和/最高/最低"时，
    逐行明细的向量相似度很低，但摘要块中的聚合值可以直接命中。
    """
    columns = [str(c) for c in df.columns]
    num_rows = len(df)

    lines: list[str] = [
        f"【表格摘要: {sheet_label}】",
        f"本表包含 {num_rows} 条记录，{len(columns)} 个字段: {', '.join(columns)}。",
    ]

    # 识别数字列并计算聚合统计
    numeric_stats: list[str] = []
    for col in columns:
        # 尝试将列转为数值（dtype=str 读入的列需要转换）
        numeric_col = pd.to_numeric(df[col], errors="coerce")
        valid_count = numeric_col.notna().sum()
        # 超过半数有效数值才视为数字列
        if valid_count > num_rows * 0.5:
            col_sum = numeric_col.sum()
            col_max = numeric_col.max()
            col_min = numeric_col.min()
            col_mean = numeric_col.mean()
            # 格式化：整数值去掉小数点
            def _f(v):
                if pd.isna(v):
                    return "N/A"
                return str(int(v)) if v == int(v) else f"{v:g}"
            numeric_stats.append(
                f"  - {col}: 总和={_f(col_sum)}, 最大={_f(col_max)}, "
                f"最小={_f(col_min)}, 平均={_f(col_mean)}, 有效行数={valid_count}"
            )

    if numeric_stats:
        lines.append("数字列统计:")
        lines.extend(numeric_stats)

    # 识别分类列（唯一值少于行数一半的文本列）
    category_cols: list[str] = []
    for col in columns:
        numeric_col = pd.to_numeric(df[col], errors="coerce")
        if numeric_col.notna().sum() <= num_rows * 0.5:
            unique_vals = df[col].dropna().unique()
            if 0 < len(unique_vals) <= max(10, num_rows * 0.5):
                sample = ", ".join(str(v) for v in unique_vals[:8])
                category_cols.append(f"  - {col}: {sample}")

    if category_cols:
        lines.append("分类字段取值:")
        lines.extend(category_cols)

    lines.append("")  # 空行分隔摘要与明细
    return "\n".join(lines)


def _df_to_text(df: pd.DataFrame, sheet_label: str) -> str:
    """将单个 DataFrame 转为结构化键值对文本（含摘要块）。"""
    if df.empty:
        return f"[表格: {sheet_label}]\n(空表)\n"

    columns = [str(c) for c in df.columns]
    header_line = f"列: {', '.join(columns)}"

    # 摘要块（聚合统计 + 自然语言描述）作为第一段
    lines: list[str] = [_generate_summary(df, sheet_label)]

    # 明细数据
    lines.append(f"[表格明细: {sheet_label}]")
    lines.append(header_line)

    for i, (_, row) in enumerate(df.iterrows()):
        # 周期性重复列头，保证分块后每个 chunk 自带列上下文
        if i > 0 and i % HEADER_REPEAT_ROWS == 0:
            lines.append(f"...（续表，列头重复）{header_line}")

        pairs = []
        for col in columns:
            val = _fmt_value(row[col])
            if val != "":
                pairs.append(f"{col}={val}")
        if pairs:
            lines.append(f"第{i + 1}行: " + " | ".join(pairs))

    lines.append("")  # 结尾空行
    return "\n".join(lines)


class TabularExtractor(BaseExtractor):
    """提取 CSV 和 XLSX 表格文件为结构化文本。"""

    SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        ext = file_path.suffix.lower()
        try:
            if ext == ".csv":
                return self._extract_csv(file_path)
            elif ext == ".xlsx":
                return self._extract_xlsx(file_path)
            else:
                raise ValueError(f"不支持的表格类型: {ext}")
        except Exception as e:
            logger.error(f"表格提取失败 {file_path.name}: {type(e).__name__}: {e}")
            raise

    def _extract_csv(self, file_path: Path) -> str:
        """提取 CSV（尝试多种编码）。"""
        df = None
        last_err = None
        for enc in ("utf-8", "utf-8-sig", "shift-jis", "gbk", "latin-1"):
            try:
                df = pd.read_csv(file_path, encoding=enc, dtype=str)
                break
            except UnicodeDecodeError as e:
                last_err = e
                continue
        if df is None:
            raise ValueError(f"CSV 编码无法解析: {last_err}")

        logger.info(f"CSV 提取: {file_path.name} | {len(df)} 行 x {len(df.columns)} 列")
        return _df_to_text(df, file_path.name)

    def _extract_xlsx(self, file_path: Path) -> str:
        """提取 XLSX 的所有 sheet。"""
        # sheet_name=None 返回 {sheet_name: DataFrame}
        sheets = pd.read_excel(file_path, sheet_name=None, dtype=str)
        logger.info(
            f"XLSX 提取: {file_path.name} | {len(sheets)} 个 sheet: {list(sheets.keys())}"
        )

        parts = [f"# 表格文件: {file_path.name}", f"包含 {len(sheets)} 个工作表: {', '.join(sheets.keys())}", ""]
        for sheet_name, df in sheets.items():
            parts.append(_df_to_text(df, f"{file_path.name} / {sheet_name}"))
            parts.append("")  # sheet 间空行分隔

        return "\n".join(parts)
