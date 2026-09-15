"""归档分类器：目录画像 + 候选检索 + LLM 结构化决策。

复用点：
- LLM 调用与 JSON 容错解析：analyzer.Analyzer._call_openai_compatible /
  analyzer._extract_json（围栏剥离、截断修复）。
- embedding：components.embedder。
- 目录画像语料：documents 表中 file_path 位于 archive/ 下的文档 overview。

LLM 契约（design.md D4）：只允许引用候选 candidate_id 或在允许根下
提议 new_subdirectory，绝不产出原始路径。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from src.engine.components.analyzer import Analyzer, _extract_json
from src.engine.components.embedder import Embedder
from src.engine.components.store.models import Document
from src.engine.components.store.postgres import async_session_factory
from config.settings import settings

logger = logging.getLogger(__name__)

# 画像文本中每篇已归档文档 overview 的截断长度。
_PROFILE_OVERVIEW_CHARS = 200
# 分类 prompt 中文件摘要的截断长度。
_FILE_SUMMARY_CHARS = 3000
# 这些目录只是人工兜底状态，不代表稳定的知识分类。它们可以继续显示在
# 目录树中，但不能进入语义候选，否则冷启动后会形成“所有文件都待整理”
# 的吸附效应。
_NON_SEMANTIC_DIRECTORY_NAMES = frozenset(
    {"待整理", "待确认", "未分类", "unclassified"}
)


@dataclass(slots=True)
class FolderProfile:
    """一个候选目录的画像。"""

    candidate_id: str  # 相对 archive 根的路径，如 "财务/发票"
    path: Path  # 绝对路径
    description: str  # 画像文本（目录名 + 已归档文档 overview 聚合）
    doc_count: int = 0
    embedding: list[float] | None = None
    documents: list[ArchiveProfileDocument] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveProfileDocument:
    """目录树中可跳转到知识库详情页的归档文档摘要。"""

    id: str
    title: str
    file_type: str
    status: str
    overview: str
    created_at: str | None


@dataclass(slots=True)
class ArchiveDecision:
    """LLM 结构化决策（candidate_id 引用制）。

    confidence 为 None 仅出现在人工指定目录的决策中（不走分流）。
    """

    candidate_id: str | None
    new_subdirectory: str | None
    new_name: str | None
    confidence: float | None
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "new_subdirectory": self.new_subdirectory,
            "new_name": self.new_name,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


class ClassificationError(Exception):
    """LLM 不可用或输出不可解析 —— 永不默认执行。"""


def _cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度（K 小、768 维，无需 numpy）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class FolderProfileStore:
    """目录画像构建与 Top-K 检索；按树签名缓存，目录/文档变化时重建。"""

    def __init__(self, archive_root: Path, embedder: Embedder) -> None:
        self._root = archive_root
        self._embedder = embedder
        self._profiles: list[FolderProfile] = []
        self._signature: tuple | None = None

    async def refresh_if_changed(self) -> None:
        """目录树或归档文档数变化时重建画像并重嵌入。"""
        signature = self._tree_signature()
        if signature == self._signature:
            return
        profiles = await self._build_profiles()
        texts = [p.description for p in profiles]
        embeddings = await self._embedder.embed_batch(texts) if texts else []
        for profile, embedding in zip(profiles, embeddings):
            profile.embedding = embedding
        self._profiles = profiles
        self._signature = signature

    def _tree_signature(self) -> tuple:
        """目录列表 + 文件数（不含文档内容，重查询由 doc_count 变化触发）。"""
        if not self._root.is_dir():
            return ()
        entries = []
        for dirpath, _dirnames, filenames in self._root.walk():
            rel = Path(dirpath).relative_to(self._root).as_posix()
            entries.append((rel, len(filenames)))
        return tuple(sorted(entries))

    async def _build_profiles(self) -> list[FolderProfile]:
        """扫描 archive/ 目录 + 聚合已归档文档 overview -> 画像。"""
        # 已归档文档（file_path 在 archive 根下的已入库文档）
        archived: dict[str, list[ArchiveProfileDocument]] = {}
        async with async_session_factory() as session:
            rows = await session.execute(
                select(
                    Document.id,
                    Document.file_path,
                    Document.title,
                    Document.file_type,
                    Document.status,
                    Document.overview,
                    Document.created_at,
                ).where(
                    Document.status == "indexed",
                    Document.is_current.is_(True),
                )
            )
            root_prefix = str(self._root.resolve())
            for (
                doc_id,
                file_path,
                title,
                file_type,
                status,
                overview,
                created_at,
            ) in rows.all():
                if not file_path or not file_path.startswith(root_prefix):
                    continue
                rel_dir = str(
                    Path(file_path).parent.relative_to(self._root.resolve())
                )
                if rel_dir == ".":
                    continue  # archive 根直接散放的文件不构成目录画像
                archived.setdefault(rel_dir, []).append(
                    ArchiveProfileDocument(
                        id=str(doc_id),
                        title=title,
                        file_type=file_type,
                        status=status,
                        overview=overview or "",
                        created_at=created_at.isoformat() if created_at else None,
                    )
                )

        profiles: list[FolderProfile] = []
        for dirpath, dirnames, _filenames in self._root.walk():
            dirnames.sort()
            rel = Path(dirpath).relative_to(self._root).as_posix()
            if rel == ".":
                continue  # archive 根本身不作为候选
            documents = sorted(
                archived.get(rel, []), key=lambda document: document.title
            )
            snippets = "；".join(
                document.overview[:_PROFILE_OVERVIEW_CHARS]
                for document in documents[:5]
            )
            description = f"目录 {rel}。" + (f" 已收纳文档摘要：{snippets}" if snippets else "（尚无文档）")
            profiles.append(
                FolderProfile(
                    candidate_id=rel,
                    path=Path(dirpath).resolve(),
                    description=description,
                    doc_count=len(documents),
                    documents=documents,
                )
            )
        return profiles

    async def top_k(self, file_summary: str, k: int) -> list[FolderProfile]:
        """按文件摘要 embedding 与目录画像的余弦相似度取 Top-K。"""
        await self.refresh_if_changed()
        semantic_profiles = [
            profile
            for profile in self._profiles
            if profile.candidate_id.split("/", 1)[0].strip().lower()
            not in _NON_SEMANTIC_DIRECTORY_NAMES
        ]
        if not semantic_profiles:
            return []
        embedding = await self._embedder.embed_text(file_summary)
        ranked = sorted(
            semantic_profiles,
            key=lambda p: _cosine(embedding, p.embedding or []),
            reverse=True,
        )
        return ranked[:k]

    def profiles(self) -> list[FolderProfile]:
        return list(self._profiles)

    def by_candidate_id(self, candidate_id: str) -> FolderProfile | None:
        for profile in self._profiles:
            if profile.candidate_id == candidate_id:
                return profile
        return None


def _build_classification_prompt(
    file_name: str,
    file_summary: str,
    candidates: list[FolderProfile],
    policy_instructions: str = "",
    max_directory_depth: int = 2,
) -> str:
    candidate_lines = "\n".join(
        f'- candidate_id: "{p.candidate_id}" （已收纳 {p.doc_count} 篇文档）: {p.description}'
        for p in candidates
    ) or "（暂无候选目录）"
    return f"""你是一个团队的文件归档助手。请为下面的文件选择最合适的归档目录。

**文件名:** {file_name}

**文件内容摘要:**
{file_summary[:_FILE_SUMMARY_CHARS]}

**当前归档规则:**
{policy_instructions or "按内容选择最稳定、最可复用的目录。"}

**已有归档目录候选（只能从中选择 candidate_id）:**
{candidate_lines}

**要求:**
1. 无论候选列表是否为空，都必须同时比较“复用已有目录”和“创建新的语义目录”两种方案。
2. 如果已有候选比新建目录更合适，设置 candidate_id 为它的 candidate_id，new_subdirectory 为 null。
3. 如果文件属于独立项目或稳定主题，新建目录比复用候选更合适，则设置 candidate_id 为 null，
   并在 new_subdirectory 中提议一个新目录名；已有候选不等于必须复用：
   只能是简短的相对目录名，最多 {max_directory_depth} 级（如 "项目/XX研究"），不能以 / 开头，不能包含 .. 。
   即使当前没有候选目录，也必须优先根据文件所属项目或明确主题提议有语义的目录；
   不要使用“待整理”“待确认”“未分类”等兜底名称，除非文件内容确实完全无法判断。
4. candidate_id 和 new_subdirectory 必须且只能填写一个。
5. new_name: 为文件起一个更清晰的名字（保留原扩展名）；没有更好的名字就保留原名。
6. confidence: 你对这次归档决策的置信度，0 到 1 之间的小数。
7. rationale: 一句话说明你为何复用已有目录或为何新建目录。

请严格返回 JSON 格式，不要包含其他内容:
```json
{{
  "candidate_id": "候选目录的 candidate_id 或 null",
  "new_subdirectory": "新目录名或 null",
  "new_name": "新文件名",
  "confidence": 0.0,
  "rationale": "..."
}}
```"""


def _parse_decision(raw: str) -> ArchiveDecision:
    """解析 LLM 输出；不可解析或字段非法时抛 ClassificationError。"""
    data = _extract_json(raw)
    if data is None:
        raise ClassificationError(f"LLM 输出无法解析为 JSON: {raw[:200]}")

    confidence = data.get("confidence")
    try:
        confidence_f = float(confidence)
    except (TypeError, ValueError):
        raise ClassificationError(f"confidence 非法: {confidence!r}") from None
    if not 0.0 <= confidence_f <= 1.0:
        raise ClassificationError(f"confidence 超出 [0,1]: {confidence_f}")

    candidate_id = data.get("candidate_id")
    new_subdirectory = data.get("new_subdirectory")
    if candidate_id is not None and not isinstance(candidate_id, str):
        raise ClassificationError(f"candidate_id 非法: {candidate_id!r}")
    if new_subdirectory is not None and not isinstance(new_subdirectory, str):
        raise ClassificationError(f"new_subdirectory 非法: {new_subdirectory!r}")
    if candidate_id is None and new_subdirectory is None:
        raise ClassificationError("决策既无 candidate_id 也无 new_subdirectory")
    if candidate_id and new_subdirectory:
        raise ClassificationError(
            "candidate_id 与 new_subdirectory 只能选择一个"
        )
    if (
        new_subdirectory
        and new_subdirectory.split("/", 1)[0].strip().lower()
        in _NON_SEMANTIC_DIRECTORY_NAMES
    ):
        raise ClassificationError(
            f"新目录不能使用操作性兜底名称: {new_subdirectory}"
        )

    new_name = data.get("new_name")
    if new_name is not None and not isinstance(new_name, str):
        new_name = None

    return ArchiveDecision(
        candidate_id=candidate_id or None,
        new_subdirectory=new_subdirectory or None,
        new_name=new_name,
        confidence=confidence_f,
        rationale=str(data.get("rationale", ""))[:500],
    )


class ArchiveClassifier:
    """候选检索 + LLM 结构化决策。LLM 未配置时抛 ClassificationError。"""

    def __init__(
        self,
        profile_store: FolderProfileStore,
        analyzer: Analyzer | None = None,
    ) -> None:
        self._profiles = profile_store
        self._analyzer = analyzer or Analyzer()
        self.policy_instructions = ""
        self.max_directory_depth = 2

    async def classify(
        self, file_name: str, file_summary: str, top_k: int = 5
    ) -> tuple[ArchiveDecision, list[FolderProfile]]:
        """返回 (决策, 提供给 LLM 的候选列表)。"""
        if not settings.llm.enabled:
            raise ClassificationError("LLM 未配置 (LLM_BASE_URL 为空)，无法分类")

        candidates = await self._profiles.top_k(file_summary, top_k)
        prompt = _build_classification_prompt(
            file_name,
            file_summary,
            candidates,
            policy_instructions=self.policy_instructions,
            max_directory_depth=self.max_directory_depth,
        )
        try:
            raw = await self._analyzer._call_openai_compatible(prompt)
        except Exception as exc:
            logger.warning("归档分类模型调用失败: %s", exc)
            raise ClassificationError("分类模型暂时不可用，请稍后重试") from exc
        decision = _parse_decision(raw)

        # candidate_id 必须引用提供的候选；否则视为提议新目录处理。
        if decision.candidate_id is not None:
            offered_ids = {candidate.candidate_id for candidate in candidates}
            if decision.candidate_id not in offered_ids:
                logger.warning(
                    "LLM 引用了未提供的候选 %r，降级为待审",
                    decision.candidate_id,
                )
                raise ClassificationError(
                    f"LLM 引用了未提供的候选: {decision.candidate_id}"
                )
        return decision, candidates
