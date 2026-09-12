"""One-shot image deck generation for the chat Agent skill."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

from config.settings import settings
from src.agent.artifacts import artifacts_root, publish_pptx
from src.engine.components.store.models import Document
from src.engine.trusted_scope import ScopeBinding

from .assembly import Assembler
from .composition import compose, regions, render_text
from .contracts import DeckSpec
from .provider import ImageProviderError, SeedreamProvider
from .quality import VisualReviewer


class ExecutionStore:
    """Path guard used by the existing QA and pinned assembly adapters."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("Invalid PPT execution path")
        return path


def _source_fingerprint(row: Document) -> str:
    value = [row.raw_text, row.title, row.version_number, row.is_current]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def _load_sources(spec: dict, binding: ScopeBinding, *, sessions=None):
    if sessions is None:
        from src.engine.components.store.postgres import async_session_factory

        sessions = async_session_factory

    reference_ids = {
        identifier
        for page in spec["pages"]
        for identifier in page["reference_document_ids"]
    }
    identifiers = set(spec["source_document_ids"]) | reference_ids
    if len(identifiers) > 20:
        raise ValueError("Too many source documents")

    records: dict[str, dict] = {}
    reference_data: dict[str, bytes] = {}
    async with sessions() as session:
        for identifier in sorted(identifiers):
            try:
                document_id = uuid.UUID(identifier)
            except ValueError as exc:
                raise PermissionError("Source unavailable") from exc
            row = await session.get(Document, document_id)
            if row is None or not binding.scope().permits(row.bank_id, row.tags):
                raise PermissionError("Source unavailable")
            record = {
                "id": identifier,
                "hash": _source_fingerprint(row),
                "reference": identifier in reference_ids,
            }
            if identifier in reference_ids:
                path = Path(row.file_path or "").resolve()
                upload_root = Path(settings.uploads_dir).resolve()
                if not path.is_relative_to(upload_root) or not path.is_file():
                    raise PermissionError("Reference image unavailable")
                data = path.read_bytes()
                from .provider import validate_image

                validate_image(data)
                record["image_hash"] = hashlib.sha256(data).hexdigest()
                reference_data[identifier] = data
            records[identifier] = record
    for page in spec["pages"]:
        regions(page["reference_document_ids"])
    return records, reference_data


def _prompt(spec: dict, page: dict, repair: str | None) -> str:
    if page["reference_document_ids"]:
        return (
            "生成16:9演示文稿底图，2560×1440。中间绝大部分留空，"
            "这是供程序随后嵌入真实原图和文字的无字底图，不是完整成品。"
            "中央从y=216到1224的全部区域只填纯背景色。整张图片禁止任何文字、"
            "字母、数字、符号、卡片、边框、图表或占位提示，不要生成页码。\n"
            + json.dumps(
                {
                    "style": spec["style"],
                    "page_role": page["layout"],
                    "empty_reference_regions": regions(
                        page["reference_document_ids"]
                    ),
                    "repair": repair,
                },
                ensure_ascii=False,
            )
        )
    return json.dumps(
        {
            "task": "Generate a polished 16:9 presentation background at 2560x1440. Do not render any text, letters, numbers, symbols, logos, charts, page numbers, frames, or placeholder glyphs. Keep the center visually calm because exact slide text will be rasterized locally.",
            "style": spec["style"],
            "context": spec["context"],
            "title": page["title"],
            "points": page["points"],
            "layout": page["layout"],
            "repair": repair,
        },
        ensure_ascii=False,
    )


def _tokens(usage) -> int | None:
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    return total if type(total) is int and total >= 0 else None


async def generate_image_ppt(
    spec: DeckSpec,
    binding: ScopeBinding,
    *,
    file_name: str | None = None,
    max_image_attempts: int | None = None,
    provider=None,
    reviewer_factory=VisualReviewer,
    assembler_factory=Assembler,
) -> dict:
    """Generate, verify, assemble and publish one deck within this invocation."""
    if not settings.ppt.enabled:
        raise ValueError("Image presentations are disabled")
    settings.image.require_ready()
    if len(spec.pages) > settings.ppt.max_pages:
        raise ValueError("PPT page limit exceeded")
    limit = max_image_attempts or 2 * len(spec.pages)
    if limit < len(spec.pages) or limit > 2 * len(spec.pages):
        raise ValueError("Image attempts must be between one and two per slide")

    value = spec.model_dump()
    source_records, reference_data = await _load_sources(value, binding)
    image_provider = provider or SeedreamProvider(settings.image)
    accounting = {
        "image_attempts": 0,
        "qa_attempts": 0,
        "tokens": 0,
        "unknown_usage": 0,
        "afp": "unknown",
        "cost": "unknown",
    }
    root = artifacts_root()
    root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".ppt-run-", dir=root) as temporary:
        store = ExecutionStore(Path(temporary))
        reviewer = reviewer_factory(store)
        assembler = assembler_factory(store)
        page_results = []
        sample: bytes | None = None

        for number, page in enumerate(value["pages"], 1):
            required = tuple(
                reference_data[identifier]
                for identifier in page["reference_document_ids"]
            )
            last_reason = None
            accepted = None
            for attempt in range(2):
                if accounting["image_attempts"] >= limit:
                    raise RuntimeError(
                        f"ppt_budget_exhausted: page {number} was not generated"
                    )
                accounting["image_attempts"] += 1
                style_reference = (sample,) if sample is not None and not required else ()
                generated = await image_provider.generate(
                    _prompt(value, page, last_reason), references=style_reference
                )
                used = _tokens(generated.usage)
                if used is None:
                    accounting["unknown_usage"] += 1
                else:
                    accounting["tokens"] += used

                folder = store.path(f"page-{number}-attempt-{attempt + 1}")
                folder.mkdir(parents=True, exist_ok=False)
                background = folder / (
                    "background.png"
                    if generated.mime == "image/png"
                    else "background.jpg"
                )
                background.write_bytes(generated.data)
                data, embedded = await asyncio.to_thread(
                    compose,
                    render_text(generated.data, page),
                    page["reference_document_ids"],
                    required,
                )
                slide = folder / "slide.png"
                slide.write_bytes(data)
                result = {
                    "path": str(slide.relative_to(store.root)).replace("\\", "/"),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mime": "image/png",
                    "background": {
                        "path": str(background.relative_to(store.root)).replace(
                            "\\", "/"
                        ),
                        "sha256": generated.sha256,
                    },
                    "embedded": embedded,
                    "requested_model": generated.requested_model,
                    "actual_model": generated.actual_model,
                    "request_id": generated.request_id,
                    "usage": generated.usage,
                }
                claim = {
                    "result": result,
                    "spec": value,
                    "page": number,
                }
                qa_references = (*required, *((sample,) if sample is not None else ()))
                accounting["qa_attempts"] += 1
                review = await reviewer(claim, qa_references)
                qa_used = _tokens(review.get("usage"))
                if qa_used is None:
                    accounting["unknown_usage"] += 1
                else:
                    accounting["tokens"] += qa_used
                if review.get("passed") is True:
                    accepted = SimpleNamespace(
                        number=number,
                        status="accepted",
                        result=result,
                        qa=review,
                    )
                    if sample is None:
                        sample = generated.data
                    break
                last_reason = str(review.get("reason") or "visual_check_failed")[:2000]
            if accepted is None:
                raise RuntimeError(json.dumps({
                    "error": "visual_check_failed",
                    "page": number,
                    "reason": last_reason or "unknown",
                    "accounting": accounting,
                }, ensure_ascii=False))
            page_results.append(accepted)

        current_records, _ = await _load_sources(value, binding)
        if current_records != source_records:
            raise PermissionError("PPT source changed or authorization was revoked")

        job = SimpleNamespace(id="chat", revision=1, spec=value)
        assembled = await asyncio.to_thread(assembler, job, page_results)
        source = store.path(assembled["path"])
        artifact = await asyncio.to_thread(
            publish_pptx,
            source,
            title=spec.title,
            file_name=file_name,
            scope=binding.scope(),
            write_tags=binding.write_tags,
        )
        return {
            **asdict(artifact),
            "pages": len(page_results),
            "image_slide_editability": "not_individually_editable",
            "accounting": accounting,
            "model": page_results[-1].result.get("actual_model")
            or page_results[-1].result["requested_model"],
            "render": assembled["render"],
            "stages": ["planned", "generated", "quality_checked", "assembled"],
        }


def public_error(error: Exception) -> str:
    if isinstance(error, ImageProviderError):
        suffix = " (provider outcome may have been billed)" if error.unknown else ""
        return f"{error.code}{suffix}"
    return str(error)
