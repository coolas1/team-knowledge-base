"""LLM 分析器：单次调用 LLM 生成 overview + 实体 + 关系。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_SCHEMA_PATH = Path("config/engine/graphrag/entity_schema.yaml")


@dataclass
class Entity:
    name: str
    type: str
    description: str = ""


@dataclass
class Relation:
    from_name: str
    to_name: str
    type: str
    description: str = ""


@dataclass
class FileRelation:
    related_doc_title: str
    type: str
    reason: str = ""


@dataclass
class ChunkAnalysisResult:
    """单个 chunk 的 LLM 分析结果。"""
    chunk_index: int
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


@dataclass
class AnalysisResult:
    overview: str
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    file_relations: list[FileRelation] = field(default_factory=list)


@dataclass
class ChangeAnalysisResult:
    """相邻版本 diff 的 LLM 分析结果。"""

    summary: str
    changes: list[dict] = field(default_factory=list)


def _load_entity_schema(path: Path) -> dict:
    """加载 entity_schema.yaml。"""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _repair_truncated_json(text: str) -> str | None:
    """尽力修复被 max_tokens 截断的 JSON。

    补齐未闭合的字符串和括号/花括号，去掉悬空的尾逗号。
    只是兜底手段：不保证语义完整，但能让"正文大部分都在"
    的截断响应不至于整个丢弃。
    """
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}" and stack:
            stack.pop()

    repaired = text
    if in_string:
        repaired += '"'
    repaired = repaired.rstrip()
    if repaired.endswith(","):
        repaired = repaired[:-1]
    if not stack:
        return repaired if repaired != text else None
    return repaired + "".join("]" if c == "[" else "}" for c in reversed(stack))


def _extract_json(raw: str) -> dict | None:
    """从 LLM 返回中提取 JSON 对象。

    容忍常见的不规范输出：```json 围栏、围栏外的说明文字、
    未闭合的围栏（响应被 max_tokens 截断时会出现）。
    """
    text = raw.strip()
    if not text:
        return None

    candidates: list[str] = []
    # 已闭合的围栏内容优先。
    candidates.extend(
        m.strip() for m in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    )
    # 围栏未闭合时，取第一行围栏标记之后的全部内容。
    if text.startswith("```") and "\n" in text:
        candidates.append(text.split("\n", 1)[1].strip())
    # 整段原文；再退而求其次，取最外层花括号之间的内容
    # （跳过围栏前后的说明文字）。
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    # 全部失败：尝试修复截断的 JSON。
    for candidate in candidates:
        repaired = _repair_truncated_json(candidate)
        if repaired is None:
            continue
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _build_prompt(text: str, title: str, schema: dict) -> str:
    """根据 entity_schema 动态生成分析 prompt。"""
    entity_types = schema.get("entity_types", {})
    relation_types = schema.get("relation_types", {})

    core_entities = ", ".join(entity_types.get("core", []))
    core_relations = ", ".join(relation_types.get("core", []))
    open_entities = entity_types.get("open", True)
    open_relations = relation_types.get("open", True)

    entity_instruction = f"核心实体类型: [{core_entities}]"
    if open_entities:
        entity_instruction += "。你也可以根据内容补充自定义实体类型。"

    relation_instruction = f"核心关系类型: [{core_relations}]"
    if open_relations:
        relation_instruction += "。你也可以根据内容补充自定义关系类型。"

    return f"""你是一个专业的文档分析助手，负责分析园区运营团队的文档内容。

请分析以下文档，返回结构化结果。

**文档标题:** {title}

**文档内容:**
{text[:8000]}

**要求:**

1. **overview**: 写一段 2-3 句话的文档摘要，概括文档的核心内容和关键信息。

2. **entities**: 提取文档中的重要实体。
   {entity_instruction}
   每个实体包含: name(名称), type(类型), description(简要描述)

3. **relations**: 提取实体之间的关系。
   {relation_instruction}
   每个关系包含: from_name(起始实体), to_name(目标实体), type(关系类型), description(关系描述)

4. **file_relations**: 如果文档中提到了与其他文档/文件相关的内容，推测可能的文件关联。
   每个关联包含: related_doc_title(相关文档标题), type(关联类型如 REFERENCES/SAME_TOPIC/ANALYZES), reason(关联原因)

请严格返回 JSON 格式，不要包含其他内容:
```json
{{
  "overview": "...",
  "entities": [{{"name": "...", "type": "...", "description": "..."}}],
  "relations": [{{"from_name": "...", "to_name": "...", "type": "...", "description": "..."}}],
  "file_relations": [{{"related_doc_title": "...", "type": "...", "reason": "..."}}]
}}
```"""


class Analyzer:
    """LLM 分析器，支持 Ollama 和 OpenAI 兼容 API。"""

    def __init__(self, schema_path: Path | None = None) -> None:
        self._schema_path = schema_path or _DEFAULT_SCHEMA_PATH
        self._schema = _load_entity_schema(self._schema_path)

    async def analyze(self, text: str, title: str) -> AnalysisResult:
        """分析文档，返回 overview + 实体 + 关系。

        如果 LLM 未配置（provider: todo），返回空结果占位。
        """
        provider = settings.llm_provider
        if provider == "todo":
            # LLM 未配置，返回占位结果
            return AnalysisResult(
                overview=f"[待 LLM 生成] {title}",
                entities=[],
                relations=[],
                file_relations=[],
            )

        prompt = _build_prompt(text, title, self._schema)

        if provider == "ollama":
            raw = await self._call_ollama(prompt)
        elif provider in ("openai", "custom"):
            raw = await self._call_openai_compatible(prompt)
        else:
            return AnalysisResult(overview=f"[未知 provider: {provider}] {title}")

        return self._parse_response(raw)

    # ── chunk 级分析 ─────────────────────────────────────────────

    async def analyze_chunk(
        self, chunk_text: str, doc_title: str, chunk_index: int
    ) -> ChunkAnalysisResult:
        """对单个 chunk 做实体和关系抽取。"""
        provider = settings.llm_provider
        if provider == "todo":
            return ChunkAnalysisResult(chunk_index=chunk_index)

        prompt = self._build_chunk_prompt(chunk_text, doc_title, self._schema)

        if provider == "ollama":
            raw = await self._call_ollama(prompt)
        elif provider in ("openai", "custom"):
            raw = await self._call_openai_compatible(prompt)
        else:
            return ChunkAnalysisResult(chunk_index=chunk_index)

        return self._parse_chunk_response(raw, chunk_index)

    # ── overview 级分析 ──────────────────────────────────────────

    async def analyze_overview(
        self, text: str, title: str
    ) -> AnalysisResult:
        """文档级分析，仅提取 overview + file_relations。"""
        provider = settings.llm_provider
        if provider == "todo":
            return AnalysisResult(
                overview=f"[待 LLM 生成] {title}",
                entities=[],
                relations=[],
                file_relations=[],
            )

        prompt = self._build_overview_prompt(title, text)

        if provider == "ollama":
            raw = await self._call_ollama(prompt)
        elif provider in ("openai", "custom"):
            raw = await self._call_openai_compatible(prompt)
        else:
            return AnalysisResult(overview=f"[未知 provider] {title}")

        return self._parse_overview_response(raw)

    # ── 版本变更分析（LLM diff）────────────────────────────────────

    async def analyze_changes(
        self, old_text: str, new_text: str, title: str
    ) -> ChangeAnalysisResult:
        """对比相邻两个版本的文本，抽取结构化变更。

        Prompt 设计参考 VersionRAG：只提取实质内容变更，
        过滤排版/标点/空白等非实质差异。
        """
        provider = settings.llm_provider
        if provider == "todo":
            return ChangeAnalysisResult(
                summary=f"[待 LLM 生成] {title} 版本变更"
            )

        prompt = self._build_changes_prompt(old_text, new_text, title)

        if provider == "ollama":
            raw = await self._call_ollama(prompt)
        elif provider in ("openai", "custom"):
            raw = await self._call_openai_compatible(prompt)
        else:
            return ChangeAnalysisResult(summary=f"[未知 provider] {title}")

        return self._parse_changes_response(raw)

    @staticmethod
    def _build_changes_prompt(old_text: str, new_text: str, title: str) -> str:
        """构建版本 diff prompt。"""
        return f"""你是一个专业的文档版本对比助手。请对比文档「{title}」的两个版本，提取结构化变更。

**旧版本内容:**
{old_text[:8000]}

**新版本内容:**
{new_text[:8000]}

**要求:**
1. **summary**: 一句话概括本次版本变更的核心内容。
2. **changes**: 列出所有实质性内容变更。
   - 只提取有意义的变更：新增/删除/修改的字段、章节、数值、结论、定义等
   - 忽略非实质变更：排版、格式、标点、空白、页码、字体、大小写等不影响含义的差异
   - 每个变更包含: name(简短标题), description(详细说明，包含具体的字段名/数值), status(added/removed/modified)

请严格返回 JSON 格式:
```json
{{
  "summary": "...",
  "changes": [
    {{"name": "...", "description": "...", "status": "added|removed|modified"}}
  ]
}}
```"""

    @staticmethod
    def _parse_changes_response(raw: str) -> ChangeAnalysisResult:
        """解析版本 diff 响应。"""
        data = _extract_json(raw)
        if data is None:
            return ChangeAnalysisResult(summary=f"[LLM 返回解析失败] {raw[:200]}")

        changes: list[dict] = []
        for c in data.get("changes", []):
            if not isinstance(c, dict):
                continue
            status = c.get("status", "modified")
            if status not in ("added", "removed", "modified"):
                status = "modified"
            changes.append(
                {
                    "name": str(c.get("name", "")),
                    "description": str(c.get("description", "")),
                    "status": status,
                }
            )
        return ChangeAnalysisResult(
            summary=str(data.get("summary", "")), changes=changes
        )

    async def _call_ollama(self, prompt: str) -> str:
        """通过 Ollama /api/generate 调用。"""
        # Ollama's native API lives at /api/* and must not inherit the
        # OpenAI-compatible LLM_BASE_URL (which commonly ends in /v1).
        base_url = settings.ollama_base_url.rstrip("/")
        model = settings.llm_model or "llama3"

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["response"]

    async def _call_openai_compatible(self, prompt: str) -> str:
        """通过 OpenAI 兼容 API 调用。

        推理型模型偶发把输出预算全部耗在思考上（content 为空），
        空响应时自动重试。
        """
        base_url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        model = settings.llm_model or "gpt-4o-mini"
        api_key = settings.llm_api_key

        content = ""
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        # 推理型模型（如 glm-5.3）会先消耗输出预算做思考，
                        # 不设上限时 JSON 正文可能被截断。
                        "max_tokens": 8192,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"] or ""
                if content.strip():
                    return content
                logger.warning(
                    f"LLM 返回空 content（第 {attempt + 1}/3 次尝试）"
                )
        return content

    @staticmethod
    def _parse_response(raw: str) -> AnalysisResult:
        """解析 LLM 返回的 JSON。"""
        data = _extract_json(raw)
        if data is None:
            return AnalysisResult(overview=f"[LLM 返回解析失败] {raw[:200]}")

        entities = [
            Entity(
                name=e.get("name", ""),
                type=e.get("type", "Unknown"),
                description=e.get("description", ""),
            )
            for e in data.get("entities", [])
        ]
        relations = [
            Relation(
                from_name=r.get("from_name", ""),
                to_name=r.get("to_name", ""),
                type=r.get("type", "RELATED_TO"),
                description=r.get("description", ""),
            )
            for r in data.get("relations", [])
        ]
        file_relations = [
            FileRelation(
                related_doc_title=f.get("related_doc_title", ""),
                type=f.get("type", "REFERENCES"),
                reason=f.get("reason", ""),
            )
            for f in data.get("file_relations", [])
        ]

        return AnalysisResult(
            overview=data.get("overview", ""),
            entities=entities,
            relations=relations,
            file_relations=file_relations,
        )

    # ── chunk prompt / parse ─────────────────────────────────────

    @staticmethod
    def _build_chunk_prompt(chunk_text: str, doc_title: str, schema: dict) -> str:
        """构建 chunk 级分析 prompt。"""
        entity_types = schema.get("entity_types", {})
        relation_types = schema.get("relation_types", {})
        core_entities = ", ".join(entity_types.get("core", []))
        core_relations = ", ".join(relation_types.get("core", []))
        open_entities = entity_types.get("open", True)
        open_relations = relation_types.get("open", True)

        entity_instruction = f"核心实体类型: [{core_entities}]"
        if open_entities:
            entity_instruction += "。你也可以根据内容补充自定义实体类型。"
        relation_instruction = f"核心关系类型: [{core_relations}]"
        if open_relations:
            relation_instruction += "。你也可以根据内容补充自定义关系类型。"

        return f"""你是一个专业的文档分析助手。请分析以下文档片段中的实体和关系。

**所属文档:** {doc_title}

**片段内容:**
{chunk_text[:4000]}

**要求:**
1. **entities**: 提取片段中的重要实体。
   {entity_instruction}
   每个实体包含: name(名称), type(类型), description(简要描述)

2. **relations**: 提取实体之间的关系。
   {relation_instruction}
   每个关系包含: from_name(起始实体), to_name(目标实体), type(关系类型), description(关系描述)

请严格返回 JSON 格式:
```json
{{
  "entities": [{{"name": "...", "type": "...", "description": "..."}}],
  "relations": [{{"from_name": "...", "to_name": "...", "type": "...", "description": "..."}}]
}}
```"""

    @staticmethod
    def _parse_chunk_response(raw: str, chunk_index: int) -> ChunkAnalysisResult:
        """解析 chunk 级 LLM 返回的 JSON。"""
        data = _extract_json(raw)
        if data is None:
            logger.warning(f"chunk {chunk_index} LLM 返回解析失败: {raw[:100]}")
            return ChunkAnalysisResult(chunk_index=chunk_index)

        entities = [
            Entity(
                name=e.get("name", ""),
                type=e.get("type", "Unknown"),
                description=e.get("description", ""),
            )
            for e in data.get("entities", [])
        ]
        relations = [
            Relation(
                from_name=r.get("from_name", ""),
                to_name=r.get("to_name", ""),
                type=r.get("type", "RELATED_TO"),
                description=r.get("description", ""),
            )
            for r in data.get("relations", [])
        ]
        return ChunkAnalysisResult(
            chunk_index=chunk_index, entities=entities, relations=relations
        )

    # ── overview prompt / parse ──────────────────────────────────

    @staticmethod
    def _build_overview_prompt(title: str, text: str) -> str:
        """构建文档级 overview + file_relations prompt。"""
        return f"""你是一个专业的文档分析助手。请为以下文档生成摘要和跨文档关联推测。

**文档标题:** {title}

**文档内容:**
{text[:8000]}

**要求:**
1. **overview**: 写一段 2-3 句话的文档摘要。
2. **file_relations**: 如果文档提到了与其他文档/文件相关的内容，推测可能的文件关联。
   每个关联包含: related_doc_title(相关文档标题), type(关联类型如 REFERENCES/SAME_TOPIC/ANALYZES), reason(关联原因)

请严格返回 JSON 格式:
```json
{{
  "overview": "...",
  "file_relations": [{{"related_doc_title": "...", "type": "...", "reason": "..."}}]
}}
```"""

    @staticmethod
    def _parse_overview_response(raw: str) -> AnalysisResult:
        """解析 overview + file_relations 响应。"""
        data = _extract_json(raw)
        if data is None:
            return AnalysisResult(overview=f"[LLM 返回解析失败] {raw[:200]}")

        file_relations = [
            FileRelation(
                related_doc_title=f.get("related_doc_title", ""),
                type=f.get("type", "REFERENCES"),
                reason=f.get("reason", ""),
            )
            for f in data.get("file_relations", [])
        ]
        return AnalysisResult(
            overview=data.get("overview", ""),
            file_relations=file_relations,
        )


# 全局单例
analyzer = Analyzer()
