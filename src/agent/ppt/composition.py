"""Versioned, review-visible reference regions and lossless asset composition."""

import hashlib
import io

from PIL import Image, ImageOps

from .provider import SLIDE_SIZE, validate_image

POLICY = "reference-composite-v1"


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
