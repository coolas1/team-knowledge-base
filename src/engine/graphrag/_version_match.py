"""文档相似度检测：识别改名后的同一文档（新版本）。

场景：用户修改了文件内容又改了文件名（规范v1.md -> 规范v2.md），
标题匹配失效。这里用文本相似度作兜底信号：

1. 精确去重：content_hash 相同（文件只是重命名，内容未动）
2. 结构相似：标题相似度（编辑距离比）+ 内容相似度（字符 shingle
   的 Jaccard）加权，阈值以上判定为"疑似同一文档的修订版"

相似度只产生「确认提议」而非自动挂链——内容相似的文档未必是
新版本（模板文档、同主题不同文档都会相似），最终由调用方/
用户确认。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[\w一-鿿]+")

# 经验阈值：标题 + 内容加权分超过它即视为疑似同文档。
# 0.70 兼顾"改动幅度 <30%"的常见修订；模板/同题文档通常落在 0.3-0.5。
SIMILARITY_THRESHOLD = 0.70
# 标题权重：改名场景标题相似度天然低，权重让位于内容。
TITLE_WEIGHT = 0.3
CONTENT_WEIGHT = 0.7


def _shingles(text: str, size: int = 3) -> set[str]:
    """字符 shingle 集合（对中文友好，无需分词）。"""
    normalized = "".join(_WORD_RE.findall(text))
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_similarity(a: str, b: str) -> float:
    """标题相似度：shingle Jaccard。

    对『规范v1.md vs 规范v2.md』这种后缀变化，大部分 shingle 保留，
    得分接近 1；完全改写得分的下限由内容相似度兜底。
    """
    return _jaccard(_shingles(a, 2), _shingles(b, 2))


def content_similarity(a: str, b: str) -> float:
    """正文相似度（shingle Jaccard，0~1）。"""
    return _jaccard(_shingles(a), _shingles(b))


def _strip_version_markers(title: str) -> str:
    """剥离标题中的版本痕迹（v1/v2/final/draft/日期等）再比较。

    "报告_v2.md" 与 "报告_final.md" 剥离后同为 "报告.md"。
    """
    return re.sub(
        r"[\s._-]*(v\d+(\.\d+)*|version\d*|final|draft|revision\d*|r\d+|\d{4}[-_.]?\d{1,2}[-_.]?\d{1,2}|\d+)",
        "",
        title,
        flags=re.IGNORECASE,
    )


def combined_similarity(
    title_a: str, text_a: str, title_b: str, text_b: str
) -> float:
    """标题 + 内容加权相似度。"""
    title_sim = max(
        _title_similarity(title_a, title_b),
        _title_similarity(_strip_version_markers(title_a), _strip_version_markers(title_b)),
    )
    return TITLE_WEIGHT * title_sim + CONTENT_WEIGHT * content_similarity(text_a, text_b)


@dataclass
class VersionMatchCandidate:
    """疑似同文档候选（改名识别的提议产物）。"""

    doc_id: str
    title: str
    similarity: float
    exact_content: bool  # content_hash 相同（纯重命名）

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "similarity": round(self.similarity, 3),
            "exact_content": self.exact_content,
        }


def find_version_candidate(
    new_title: str,
    new_text: str,
    existing: list[tuple[str, str, str]],  # (doc_id, title, raw_text)
    threshold: float = SIMILARITY_THRESHOLD,
) -> VersionMatchCandidate | None:
    """在当前版文档中寻找疑似同一文档的候选。

    返回相似度最高且超过阈值的候选；无则 None（按全新文档处理）。
    """
    new_hash = hashlib.sha256(new_text.encode()).hexdigest()
    best: VersionMatchCandidate | None = None
    for doc_id, title, raw_text in existing:
        exact = hashlib.sha256((raw_text or "").encode()).hexdigest() == new_hash
        if exact:
            # 内容完全一致：无论标题如何都是同一文档的纯重命名。
            candidate = VersionMatchCandidate(
                doc_id=doc_id, title=title, similarity=1.0, exact_content=True
            )
            return candidate
        sim = combined_similarity(new_title, new_text, title, raw_text or "")
        if sim >= threshold and (best is None or sim > best.similarity):
            best = VersionMatchCandidate(
                doc_id=doc_id, title=title, similarity=sim, exact_content=False
            )
    return best
