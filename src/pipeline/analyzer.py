"""LLM 分析器：单次调用 LLM 生成 overview + 实体 + 关系。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from src.db.config import settings

logger = logging.getLogger(__name__)

_ENTITY_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "config" / "entity_schema.yaml"


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


def _load_entity_schema() -> dict:
    """加载 entity_schema.yaml。"""
    if _ENTITY_SCHEMA_PATH.exists():
        with open(_ENTITY_SCHEMA_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


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

    def __init__(self) -> None:
        schema = _load_entity_schema()
        self._schema = schema
        # 从 model_config.yaml 读取 LLM 配置
        self._config = self._load_model_config()

    @staticmethod
    def _load_model_config() -> dict:
        config_path = Path(__file__).resolve().parents[2] / "config" / "model_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                return config.get("llm", {})
        return {}

    async def analyze(self, text: str, title: str) -> AnalysisResult:
        """分析文档，返回 overview + 实体 + 关系。

        如果 LLM 未配置（provider: todo），返回空结果占位。
        """
        provider = self._config.get("provider", "todo")
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
        provider = self._config.get("provider", "todo")
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
        provider = self._config.get("provider", "todo")
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

    async def _call_ollama(self, prompt: str) -> str:
        """通过 Ollama /api/generate 调用。"""
        base_url = self._config.get("base_url", settings.ollama_base_url).rstrip("/")
        model = self._config.get("model", "llama3")

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    # Qwen3 默认把推理过程放进独立 thinking 字段，
                    # 关闭思考模式以确保结构化 JSON 返回在 response 中。
                    "think": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["response"]

    async def _call_openai_compatible(self, prompt: str) -> str:
        """通过 OpenAI 兼容 API 调用。"""
        base_url = self._config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        model = self._config.get("model", "gpt-4o-mini")
        # 优先从 model_config.yaml 读取，为空则 fallback 到 .env 的 LLM_API_KEY
        api_key = self._config.get("api_key", "") or settings.llm_api_key

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
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_response(raw: str) -> AnalysisResult:
        """解析 LLM 返回的 JSON。"""
        try:
            # 尝试提取 JSON（LLM 可能会包裹在 ```json ``` 中）
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            data = json.loads(text)
        except json.JSONDecodeError:
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
        try:
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            data = json.loads(text)
        except json.JSONDecodeError:
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
        try:
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            data = json.loads(text)
        except json.JSONDecodeError:
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
