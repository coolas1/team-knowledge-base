import hashlib
import io
import json
import random
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from config.settings import settings
from src.agent.ppt.assembly import Assembler, validate_pptx
from src.agent.ppt.quality import VisualReviewer, validate_review
from src.agent.ppt.generator import ExecutionStore
from src.agent.ppt.composition import compose


@pytest.mark.parametrize("field", ["text", "numbers", "assets", "style", "layout"])
def test_any_failed_visual_check_prevents_acceptance(field):
    value = dict(
        text=True, numbers=True, assets=True, style=True, layout=True, reason="检查说明"
    )
    value[field] = False
    assert validate_review(value)["passed"] is False
    value.pop(field)
    with pytest.raises(ValueError):
        validate_review(value)


async def test_qa_transmits_slide_and_required_asset(monkeypatch, tmp_path):
    out = io.BytesIO()
    Image.new("RGB", (2560, 1440), "red").save(out, format="PNG")
    data, embedded = compose(out.getvalue(), ["ref"], (out.getvalue(),))
    (tmp_path / "slide.png").write_bytes(data)
    monkeypatch.setattr(
        settings.llm, "base_url", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )
    monkeypatch.setattr(settings.llm, "model", "doubao-seed-2.1-turbo")

    def handle(request):
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        assert len([x for x in content if x["type"] == "image_url"]) == 2
        assert body["max_tokens"] == 1024 and body["thinking"]["type"] == "disabled"
        return httpx.Response(
            200,
            json={
                "model": "fixture",
                "usage": {"total_tokens": 100},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                dict(
                                    text=True,
                                    numbers=False,
                                    assets=True,
                                    style=True,
                                    layout=True,
                                    reason="金额错误",
                                )
                            )
                        }
                    }
                ],
            },
        )

    review = VisualReviewer(
        ExecutionStore(tmp_path), transport=httpx.MockTransport(handle)
    )
    result = await review(
        {
            "result": {"path": "slide.png", "embedded": embedded},
            "spec": {
                "style": "red",
                "pages": [
                    {
                        "title": "预算",
                        "points": ["120万元"],
                        "reference_document_ids": ["ref"],
                    }
                ],
            },
            "page": 1,
        },
        (out.getvalue(),),
    )
    assert result["passed"] is False and result["reason"] == "金额错误"


@pytest.mark.parametrize("large_image", [False, True])
def test_assembly_preserves_pages_notes_and_refuses_unverified_image(
    monkeypatch, tmp_path, large_image
):
    from src.agent.ppt import assembly

    store = ExecutionStore(tmp_path)
    spec = {
        "title": "验收",
        "style": "navy",
        "pages": [
            {"title": "一", "points": ["120万元"], "notes": "这是第一段讲稿。"},
            {"title": "二", "points": ["2026年"], "notes": "这是第二段讲稿。"},
        ],
    }
    pages = []
    for i, color in enumerate(["navy", "teal"], 1):
        out = io.BytesIO()
        image = Image.new("RGB", (2560, 1440), color)
        if large_image and i == 1:
            image = Image.frombytes(
                "RGB", (2560, 1440), random.Random(42).randbytes(2560 * 1440 * 3)
            )
        image.save(out, format="PNG")
        if large_image and i == 1:
            assert len(out.getvalue()) > 2 * 1024 * 1024
        path = tmp_path / f"{i}.png"
        path.write_bytes(out.getvalue())
        pages.append(
            SimpleNamespace(
                number=i,
                status="accepted",
                qa={"passed": True},
                result={
                    "path": path.name,
                    "sha256": hashlib.sha256(out.getvalue()).hexdigest(),
                },
            )
        )
    job = SimpleNamespace(id="test-job", revision=1, spec=spec)
    monkeypatch.setattr(
        assembly,
        "render_pptx",
        lambda path, count: {"tool": "mock-only", "pages": count},
    )
    result = Assembler(store)(job, pages)
    validate_pptx(store.path(result["path"]), pages, spec)
    assert "download_url" not in result
    pages[1].qa = {"passed": False}
    with pytest.raises(ValueError, match="visual QA"):
        Assembler(store)(job, pages)
