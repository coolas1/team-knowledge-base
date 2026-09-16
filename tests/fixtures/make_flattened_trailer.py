"""重新生成 flattened_trailer.pdf —— 一个 trailer 被压到一行的极小 PDF。

    uv run python tests/fixtures/make_flattened_trailer.py

源站点的后处理把 ``startxref``、交叉引用偏移和 ``%%EOF`` 挤到同一行，
pypdf 反向扫描 EOF 标记时因此读不到，PdfReader 构造即抛 PdfStreamError。
正文内容完好，所以修复 trailer 后应当能提取出同样的文字。
"""

from pathlib import Path

from pdf_builder import build, stream

OUTPUT = Path(__file__).with_name("flattened_trailer.pdf")

TEXT = b"Flattened trailer fixture"
CONTENT = b"BT /F1 24 Tf 72 720 Td (" + TEXT + b") Tj ET"

# 1:CATALOG 2:PAGES 3:PAGE 4:CONTENTS 5:FONT
OBJECTS = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    stream(CONTENT),
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]


if __name__ == "__main__":
    OUTPUT.write_bytes(build(OBJECTS, trailer_separator=b" "))
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
