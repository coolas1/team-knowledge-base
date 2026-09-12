import io

from PIL import Image, ImageDraw
import pytest

from src.agent.ppt.composition import (
    _font,
    _wrap,
    check_boxes,
    compose,
    regions,
    render_text,
    verify_composite,
)


def png(size, color):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def test_landscape_and_portrait_preserve_pixels_without_crop():
    refs = (png((400, 200), "red"), png((100, 500), "green"))
    data, records = compose(png((2560, 1440), "navy"), ["a", "b"], refs)
    verify_composite(data, ["a", "b"], refs, records)
    assert records[0]["box"][2] == records[0]["box"][3] * 2
    assert abs(records[1]["box"][2] * 5 - records[1]["box"][3]) <= 4
    with Image.open(io.BytesIO(data)) as image:
        assert image.getpixel((0, 0)) == (0, 0, 128)
        for record, expected in zip(records, [(255, 0, 0), (0, 128, 0)], strict=True):
            x, y, w, h = record["box"]
            assert image.getpixel((x, y)) == expected
            assert image.getpixel((x + w - 1, y + h - 1)) == expected


def test_tampered_pixels_or_source_refused():
    raw = png((400, 200), "red")
    data, records = compose(png((2560, 1440), "navy"), ["a"], (raw,))
    with Image.open(io.BytesIO(data)) as image:
        image.putpixel(tuple(records[0]["box"][:2]), (0, 0, 0))
        stream = io.BytesIO()
        image.save(stream, "PNG")
    with pytest.raises(ValueError, match="pixels changed"):
        verify_composite(stream.getvalue(), ["a"], (raw,), records)
    with pytest.raises(ValueError, match="identity"):
        verify_composite(data, ["a"], (png((400, 200), "blue"),), records)


def test_invalid_regions_and_missing_assets_refused():
    with pytest.raises(ValueError, match="distinct"):
        regions(["a", "a"])
    with pytest.raises(ValueError, match="outside"):
        check_boxes([{"box": [2500, 0, 100, 100]}])
    with pytest.raises(ValueError, match="Overlapping"):
        check_boxes([{"box": [0, 0, 100, 100]}, {"box": [50, 50, 100, 100]}])
    with pytest.raises(ValueError, match="missing"):
        compose(png((2560, 1440), "navy"), ["a"], ())


def test_mixed_text_wrap_keeps_latin_terms_intact():
    canvas = Image.new("RGB", (1000, 400), "white")
    draw = ImageDraw.Draw(canvas)
    lines = _wrap(
        draw,
        "Hindsight Worker Consolidation Mental Model Recall Reflect",
        _font(36),
        310,
    )
    assert all(
        any(term in line for line in lines)
        for term in (
            "Hindsight",
            "Worker",
            "Consolidation",
            "Mental Model",
            "Recall",
            "Reflect",
        )
    )
    assert " ".join(lines).split() == [
        "Hindsight",
        "Worker",
        "Consolidation",
        "Mental",
        "Model",
        "Recall",
        "Reflect",
    ]


def test_comparison_layout_has_two_distinct_panels():
    data = render_text(
        png((2560, 1440), "navy"),
        {
            "title": "处理方式对比",
            "points": ["旧流程", "成本高", "新流程", "按需检索"],
            "layout": "左右对比",
            "reference_document_ids": [],
        },
    )
    with Image.open(io.BytesIO(data)) as image:
        assert image.getpixel((182, 500)) != image.getpixel((1280, 500))
        assert image.getpixel((2378, 500)) != image.getpixel((1280, 500))
