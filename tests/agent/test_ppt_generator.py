from __future__ import annotations

import io
from types import SimpleNamespace
import uuid

from PIL import Image
import pytest

from config.settings import settings
from src.agent.ppt.contracts import DeckSpec
from src.agent.ppt.generator import _load_sources, generate_image_ppt
from src.agent.ppt.provider import GeneratedImage
from src.engine.trusted_scope import ScopeBinding


def slide(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2560, 1440), color).save(output, format="PNG")
    return output.getvalue()


class Provider:
    def __init__(self):
        self.references = []

    async def generate(self, prompt, *, references=()):
        self.references.append(references)
        data = slide("navy" if len(self.references) == 1 else "teal")
        return GeneratedImage(
            data=data,
            mime="image/png",
            sha256="background",
            requested_model="seedream",
            actual_model="seedream-test",
            request_id=str(len(self.references)),
            usage={"total_tokens": 10},
        )


def reviewer_factory(_store):
    async def review(_claim, _references):
        return {
            "text": True,
            "numbers": True,
            "assets": True,
            "style": True,
            "layout": True,
            "passed": True,
            "reason": "ok",
            "usage": {"total_tokens": 5},
        }

    return review


def assembler_factory(store):
    def assemble(_job, pages):
        assert len(pages) == 2
        path = store.path("assembled.pptx")
        path.write_bytes(b"verified deck")
        return {
            "path": "assembled.pptx",
            "render": {"tool": "fixture", "pages": 2},
        }

    return assemble


async def test_chat_generation_publishes_normal_artifact_and_uses_internal_sample(
    monkeypatch, tmp_path
):
    from src.agent.ppt import generator

    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(settings.ppt, "enabled", True)
    monkeypatch.setattr(settings.image, "base_url", "https://ark.cn-beijing.volces.com/api/plan/v3")
    monkeypatch.setattr(settings.image, "model", "doubao-seedream-5.0-lite")
    monkeypatch.setattr(settings.image, "api_key", "test")

    calls = 0

    async def sources(_spec, _binding):
        nonlocal calls
        calls += 1
        return {}, {}

    monkeypatch.setattr(generator, "_load_sources", sources)
    provider = Provider()
    spec = DeckSpec.model_validate(
        {
            "title": "路线图",
            "style": "深蓝简洁",
            "pages": [
                {"title": "目标", "points": ["发布"], "layout": "封面", "notes": "介绍目标"},
                {"title": "计划", "points": ["验证"], "layout": "时间线", "notes": "介绍计划"},
            ],
        }
    )

    result = await generate_image_ppt(
        spec,
        ScopeBinding(),
        file_name="roadmap",
        provider=provider,
        reviewer_factory=reviewer_factory,
        assembler_factory=assembler_factory,
    )

    assert calls == 2
    assert provider.references[0] == ()
    assert provider.references[1] and provider.references[1][0].startswith(b"\x89PNG")
    assert result["download_url"].startswith("/api/artifacts/")
    assert result["pages"] == 2
    assert result["accounting"] == {
        "image_attempts": 2,
        "qa_attempts": 2,
        "tokens": 30,
        "unknown_usage": 0,
        "afp": "unknown",
        "cost": "unknown",
    }
    assert not list(tmp_path.glob(".ppt-run-*"))


async def test_chat_generation_rejects_unbounded_attempts(monkeypatch):
    monkeypatch.setattr(settings.ppt, "enabled", True)
    monkeypatch.setattr(settings.image, "base_url", "https://ark.cn-beijing.volces.com/api/plan/v3")
    monkeypatch.setattr(settings.image, "model", "doubao-seedream-5.0-lite")
    monkeypatch.setattr(settings.image, "api_key", "test")
    spec = DeckSpec.model_validate(
        {
            "title": "测试",
            "style": "简洁",
            "pages": [
                {"title": "一", "points": ["内容"], "layout": "封面", "notes": "讲稿"}
            ],
        }
    )

    with pytest.raises(ValueError, match="between one and two"):
        await generate_image_ppt(spec, ScopeBinding(), max_image_attempts=3)


async def test_source_loading_rejects_documents_outside_chat_scope():
    document_id = uuid.uuid4()
    row = SimpleNamespace(
        id=document_id,
        bank_id="private",
        tags=[],
        raw_text="secret",
        title="private",
        version_number=1,
        is_current=True,
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _identifier):
            return row

    spec = {
        "source_document_ids": [str(document_id)],
        "pages": [{"reference_document_ids": []}],
    }
    with pytest.raises(PermissionError, match="Source unavailable"):
        await _load_sources(spec, ScopeBinding(), sessions=Session)
