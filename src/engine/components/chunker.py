"""语义段落文本分块器。

按段落边界切分，每块约 chunk_size tokens，相邻块间有 overlap tokens 重叠。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    """一个文本块。"""

    index: int
    text: str
    token_count: int


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 ~1.5 字/token，英文 ~4 字符/token。
    混合文本取平均值约 2 字符/token。
    """
    return max(1, len(text) // 2)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[Chunk]:
    """将文本按语义段落切分为 chunks。

    策略：
    1. 按双换行符（段落）分割文本
    2. 逐段落累加，直到达到 chunk_size
    3. 相邻 chunk 保留 overlap tokens 重叠

    Args:
        text: 待切分的文本
        chunk_size: 每块目标 token 数（默认 500）
        overlap: 相邻块重叠 token 数（默认 50）

    Returns:
        Chunk 列表，按顺序排列
    """
    if not text or not text.strip():
        return []

    # 按段落分割（双换行或单换行）
    paragraphs = _split_paragraphs(text)

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_index = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        # 如果单个段落就超过 chunk_size，单独作为一个 chunk
        if not current_parts and para_tokens >= chunk_size:
            chunks.append(Chunk(index=chunk_index, text=para, token_count=para_tokens))
            chunk_index += 1
            continue

        # 累加段落
        if current_tokens + para_tokens <= chunk_size:
            current_parts.append(para)
            current_tokens += para_tokens
        else:
            # 当前块已满，保存
            chunk_text_str = "\n\n".join(current_parts)
            chunks.append(
                Chunk(
                    index=chunk_index,
                    text=chunk_text_str,
                    token_count=current_tokens,
                )
            )
            chunk_index += 1

            # 保留 overlap：从当前块尾部回溯
            overlap_parts = _get_overlap_parts(current_parts, overlap)
            current_parts = overlap_parts + [para]
            current_tokens = sum(_estimate_tokens(p) for p in current_parts)

    # 处理最后一个块
    if current_parts:
        chunk_text_str = "\n\n".join(current_parts)
        chunks.append(
            Chunk(
                index=chunk_index,
                text=chunk_text_str,
                token_count=current_tokens,
            )
        )

    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """按双换行符或单换行符分割段落，去除空段落。"""
    # 先按双换行分
    raw_parts = text.split("\n\n")
    paragraphs = []
    for part in raw_parts:
        stripped = part.strip()
        if stripped:
            paragraphs.append(stripped)
    return paragraphs


def _get_overlap_parts(parts: list[str], overlap_tokens: int) -> list[str]:
    """从段落列表尾部提取约 overlap_tokens 个 token 的段落。"""
    if overlap_tokens <= 0 or not parts:
        return []

    result: list[str] = []
    tokens = 0
    for part in reversed(parts):
        part_tokens = _estimate_tokens(part)
        if tokens + part_tokens > overlap_tokens:
            break
        result.insert(0, part)
        tokens += part_tokens
    return result
