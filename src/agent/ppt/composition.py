"""Versioned, review-visible reference regions and lossless asset composition."""

import hashlib
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .provider import SLIDE_SIZE, validate_image

POLICY = "reference-composite-v1"

_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
)


def _font(size, *, bold=False):
    preferred = (1, 3, 0, 2) if bold else (0, 2, 1, 3)
    for index in preferred:
        path = Path(_FONT_CANDIDATES[index])
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("Chinese presentation font is unavailable")


def _wrap(draw, text, font, width):
    """Wrap mixed Chinese/Latin text without splitting ordinary Latin terms."""
    lines, current = [], ""
    tokens = re.findall(r"[A-Za-z0-9]+(?:[._/+:#-][A-Za-z0-9]+)*|\s+|.", text)
    for token in tokens:
        if token.isspace():
            if current and not current.endswith(" "):
                current += " "
            continue
        candidate = current + token
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(current.rstrip())
            current = token
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return lines


def _fit_lines(draw, text, width, max_lines, *, start=44, minimum=28, bold=False):
    """Choose a readable font that keeps a card's exact text within its box."""
    for size in range(start, minimum - 1, -2):
        font = _font(size, bold=bold)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    font = _font(minimum, bold=bold)
    return font, _wrap(draw, text, font, width)[:max_lines]


def _card(draw, box, number, text, *, accent=(91, 151, 255), center=False):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(
        box, 30, fill=(16, 35, 76, 255), outline=(*accent, 190), width=2
    )
    draw.rounded_rectangle(
        (x1 + 28, y1 + 28, x1 + 98, y1 + 98), 18, fill=(*accent, 235)
    )
    badge = _font(32, bold=True)
    label = str(number).zfill(2)
    badge_box = draw.textbbox((0, 0), label, font=badge)
    draw.text(
        (x1 + 63 - badge_box[2] / 2, y1 + 62 - badge_box[3] / 2),
        label,
        font=badge,
        fill=(4, 12, 35),
    )
    width = x2 - x1 - (150 if not center else 76)
    font, lines = _fit_lines(draw, text, width, 3, start=42, minimum=28)
    line_height = font.size + 14
    text_y = y1 + (y2 - y1 - line_height * len(lines)) / 2
    text_x = x1 + (128 if not center else 38)
    for line in lines:
        if center:
            bounds = draw.textbbox((0, 0), line, font=font)
            text_x = x1 + (x2 - x1 - bounds[2]) / 2
        draw.text((text_x, text_y), line, font=font, fill=(232, 241, 255))
        text_y += line_height


def _content_layout(draw, page):
    """Render purpose-specific layouts while keeping all typography exact."""
    layout = page["layout"].lower()
    points = page["points"]
    title_font, title_lines = _fit_lines(
        draw, page["title"], 2060, 2, start=68, minimum=48, bold=True
    )
    y = 120
    for line in title_lines:
        draw.text((180, y), line, font=title_font, fill="white")
        y += title_font.size + 12
    draw.rounded_rectangle((180, y + 18, 520, y + 27), 5, fill=(74, 219, 196, 255))
    top = max(330, y + 75)
    bottom = 1320

    if any(word in layout for word in ("flow", "流程", "timeline", "链路")):
        cols = min(3, len(points))
        rows = (len(points) + cols - 1) // cols
        gap = 34
        width = (2200 - gap * (cols - 1)) // cols
        height = (bottom - top - gap * (rows - 1)) // rows
        for index, point in enumerate(points):
            row, col = divmod(index, cols)
            x1 = 180 + col * (width + gap)
            y1 = top + row * (height + gap)
            _card(draw, (x1, y1, x1 + width, y1 + height), index + 1, point)
            if col < cols - 1 and index + 1 < len(points):
                cy = y1 + height // 2
                draw.line(
                    (x1 + width + 8, cy, x1 + width + gap - 8, cy),
                    fill=(74, 219, 196),
                    width=6,
                )
    elif any(word in layout for word in ("architecture", "架构", "layer", "层")):
        gap = 20
        height = (bottom - top - gap * (len(points) - 1)) // len(points)
        palette = (
            (78, 139, 255),
            (68, 187, 210),
            (76, 210, 168),
            (146, 124, 244),
            (235, 157, 74),
            (235, 98, 130),
        )
        for index, point in enumerate(points):
            inset = min(index * 36, 160)
            _card(
                draw,
                (
                    180 + inset,
                    top + index * (height + gap),
                    2380 - inset,
                    top + index * (height + gap) + height,
                ),
                index + 1,
                point,
                accent=palette[index % len(palette)],
            )
    elif any(word in layout for word in ("comparison", "对比")):
        panel_gap = 52
        panel_width = (2200 - panel_gap) // 2
        split = (len(points) + 1) // 2
        groups = (points[:split], points[split:])
        palette = ((91, 151, 255), (74, 219, 196))
        for column, group in enumerate(groups):
            x1 = 180 + column * (panel_width + panel_gap)
            x2 = x1 + panel_width
            draw.rounded_rectangle(
                (x1, top, x2, bottom),
                34,
                fill=(12, 30, 68, 255),
                outline=(*palette[column], 220),
                width=4,
            )
            draw.rounded_rectangle(
                (x1 + 30, top + 30, x2 - 30, top + 46),
                8,
                fill=(*palette[column], 255),
            )
            row_top = top + 82
            row_gap = 24
            row_height = (
                bottom - row_top - 34 - row_gap * max(0, len(group) - 1)
            ) // max(1, len(group))
            for offset, point in enumerate(group):
                y1 = row_top + offset * (row_height + row_gap)
                _card(
                    draw,
                    (x1 + 30, y1, x2 - 30, y1 + row_height),
                    (0 if column == 0 else split) + offset + 1,
                    point,
                    accent=palette[column],
                )
        divider_x = 180 + panel_width + panel_gap // 2
        draw.line(
            (divider_x, top + 42, divider_x, bottom - 42), fill=(138, 164, 205), width=4
        )
    elif any(word in layout for word in ("evidence", "证据", "summary", "总结")):
        cols = 2
        rows = (len(points) + 1) // 2
        gap = 34
        width = (2200 - gap) // 2
        height = (bottom - top - gap * (rows - 1)) // rows
        palette = ((91, 151, 255), (74, 219, 196), (170, 123, 255), (255, 179, 71))
        for index, point in enumerate(points):
            row, col = divmod(index, cols)
            x1 = 180 + col * (width + gap)
            y1 = top + row * (height + gap)
            _card(
                draw,
                (x1, y1, x1 + width, y1 + height),
                index + 1,
                point,
                accent=palette[index % len(palette)],
            )
    else:
        gap = 24
        height = (bottom - top - gap * (len(points) - 1)) // len(points)
        for index, point in enumerate(points):
            y1 = top + index * (height + gap)
            _card(
                draw,
                (180, y1, 2380, y1 + height),
                index + 1,
                point,
                accent=(91, 151, 255) if index % 2 == 0 else (74, 219, 196),
            )


def render_text(background, page):
    """Rasterize exact slide text over a model-generated visual background."""
    validate_image(background, slide=True)
    with Image.open(io.BytesIO(background)) as image:
        canvas = image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    has_references = bool(page["reference_document_ids"])
    # Background models occasionally emit decorative pseudo-text despite a
    # negative prompt. A strong style tint suppresses it while preserving the
    # generated lighting and geometry beneath the exact local typography.
    draw.rectangle((0, 0, 2560, 1440), fill=(5, 13, 42, 190))

    if has_references:
        draw.rectangle((0, 0, 2560, 215), fill=(7, 17, 49, 248))
        draw.rectangle((0, 1225, 2560, 1440), fill=(7, 17, 49, 248))
        title_font = _font(66, bold=True)
        draw.text((128, 63), page["title"], font=title_font, fill="white")
        footer = "  |  ".join(page["points"])
        size = 44
        while size > 26:
            footer_font = _font(size)
            if draw.textbbox((0, 0), footer, font=footer_font)[2] <= 2300:
                break
            size -= 2
        box = draw.textbbox((0, 0), footer, font=footer_font)
        draw.text(
            ((2560 - box[2]) / 2, 1310), footer, font=footer_font, fill=(220, 235, 255)
        )
    else:
        cover = page["layout"].lower() in {"cover", "封面"}
        if cover:
            draw.rounded_rectangle(
                (230, 250, 2330, 1160),
                54,
                fill=(5, 13, 42, 238),
                outline=(74, 219, 196, 150),
                width=3,
            )
            draw.rounded_rectangle((1080, 325, 1480, 337), 6, fill=(74, 219, 196, 255))
            title_font = _font(92, bold=True)
            title_lines = _wrap(draw, page["title"], title_font, 1960)
            y = 410
            for line in title_lines[:2]:
                box = draw.textbbox((0, 0), line, font=title_font)
                draw.text(((2560 - box[2]) / 2, y), line, font=title_font, fill="white")
                y += 125
            point_font = _font(46)
            y += 70
            for point in page["points"]:
                box = draw.textbbox((0, 0), point, font=point_font)
                draw.text(
                    ((2560 - box[2]) / 2, y),
                    point,
                    font=point_font,
                    fill=(205, 225, 255),
                )
                y += 78
        else:
            draw.rounded_rectangle(
                (90, 60, 2470, 1380),
                50,
                fill=(5, 13, 42, 255),
                outline=(69, 99, 170, 100),
                width=2,
            )
            _content_layout(draw, page)

    canvas = Image.alpha_composite(canvas, overlay).convert("RGB")
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    result = output.getvalue()
    validate_image(result, slide=True)
    return result


def regions(identifiers):
    if len(identifiers) > 3 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Choose up to three distinct reference images")
    if not identifiers:
        return []
    width = (1792 - 51 * (len(identifiers) - 1)) // len(identifiers)
    return [
        {"document_id": identifier, "box": [384 + i * (width + 51), 288, width, 864]}
        for i, identifier in enumerate(identifiers)
    ]


def check_boxes(records):
    boxes = []
    for record in records:
        x, y, w, h = record["box"]
        if any(type(v) is not int for v in (x, y, w, h)) or min(x, y) < 0:
            raise ValueError("Invalid reference rectangle")
        if min(w, h) <= 0 or x + w > SLIDE_SIZE[0] or y + h > SLIDE_SIZE[1]:
            raise ValueError("Reference rectangle outside canvas")
        if any(
            x < a + c and a < x + w and y < b + d and b < y + h for a, b, c, d in boxes
        ):
            raise ValueError("Overlapping reference rectangles")
        boxes.append((x, y, w, h))


def fitted(data, region):
    validate_image(data)
    with Image.open(io.BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source).convert("RGBA")
        # Transparent figures receive a stable white backing rather than
        # allowing generated text or decoration to show through them.
        original = Image.new("RGBA", source.size, "white")
        original.alpha_composite(source)
        x, y, w, h = region["box"]
        resized = ImageOps.contain(
            original.convert("RGB"), (w, h), Image.Resampling.LANCZOS
        )
    box = [
        x + (w - resized.width) // 2,
        y + (h - resized.height) // 2,
        resized.width,
        resized.height,
    ]
    return resized, {
        "document_id": region["document_id"],
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "box": box,
        "pixels_sha256": hashlib.sha256(resized.tobytes()).hexdigest(),
    }


def verify_pixels(data, records):
    validate_image(data, slide=True)
    check_boxes(records)
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        for record in records:
            x, y, w, h = record["box"]
            pixels = image.crop((x, y, x + w, y + h)).tobytes()
            if hashlib.sha256(pixels).hexdigest() != record["pixels_sha256"]:
                raise ValueError("Embedded reference pixels changed")


def verify_composite(data, identifiers, references, records):
    if len(identifiers) != len(references):
        raise ValueError("Required reference images missing")
    expected = [
        fitted(raw, region)[1]
        for raw, region in zip(references, regions(identifiers), strict=True)
    ]
    if expected != records:
        raise ValueError("Reference composition identity mismatch")
    verify_pixels(data, records)


def compose(background, identifiers, references):
    validate_image(background, slide=True)
    slots = regions(identifiers)
    check_boxes(slots)
    if len(slots) != len(references):
        raise ValueError("Required reference images missing")
    with Image.open(io.BytesIO(background)) as image:
        canvas = image.convert("RGB")
    records = []
    for raw, region in zip(references, slots, strict=True):
        asset, record = fitted(raw, region)
        canvas.paste(asset, record["box"][:2])
        records.append(record)
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    data = output.getvalue()
    verify_composite(data, identifiers, references, records)
    return data, records
