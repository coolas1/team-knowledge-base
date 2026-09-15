"""重新生成 surrogate_cmap.pdf —— 一个 ToUnicode 映射指向代理项的极小 PDF。

    uv run python tests/fixtures/make_surrogate_cmap.py

pypdf 用 ``errors="surrogatepass"`` 解码 ToUnicode CMap，所以把 <0001> 映到
<DBC3>（孤立高代理项）不会报错，而是产出一个无法编码为 UTF-8 的字符。
fixture 只有几百字节，直接从十六进制看内容即可复核。
"""

from pathlib import Path

OUTPUT = Path(__file__).with_name("surrogate_cmap.pdf")

# 1:CATALOG 2:PAGES 3:PAGE 4:CONTENTS 5:FONT 6:CIDFONT 7:ToUnicode 8:FONTDESC
OBJECTS = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    None,  # contents：文本 "<0001>" 经 /F1 渲染
    b"<< /Type /Font /Subtype /Type0 /BaseFont /Dummy /Encoding /Identity-H "
    b"/DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
    b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Dummy "
    b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
    b"/FontDescriptor 8 0 R /DW 1000 /W [0 [1000]] >>",
    None,  # ToUnicode CMap
    b"<< /Type /FontDescriptor /FontName /Dummy /Flags 4 /FontBBox [0 0 1000 1000] "
    b"/ItalicAngle 0 /Ascent 1000 /Descent -200 /CapHeight 1000 /StemV 80 >>",
]

CONTENT = b"BT /F1 24 Tf 72 720 Td <0001> Tj ET"
CMAP = (
    b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
    b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
    b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
    b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    b"1 beginbfchar\n<0001> <DBC3>\nendbfchar\nendcmap\n"
    b"CMapName currentdict /CMap defineresource pop\nend\nend"
)
OBJECTS[3] = b"<< /Length %d >>\nstream\n" % len(CONTENT) + CONTENT + b"\nendstream"
OBJECTS[6] = b"<< /Length %d >>\nstream\n" % len(CMAP) + CMAP + b"\nendstream"


def build() -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(OBJECTS, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(OBJECTS) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(OBJECTS) + 1,
        xref,
    )
    return bytes(out)


if __name__ == "__main__":
    OUTPUT.write_bytes(build())
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
