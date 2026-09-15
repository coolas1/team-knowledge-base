"""极简 PDF 组装器，供 tests/fixtures 下的生成脚本使用。

手写 xref 偏移既啰嗦又容易错，这里只负责把它算对。「畸形 trailer」这个
fixture 靠 ``trailer_separator`` 控制 startxref / 偏移 / %%EOF 之间的分隔符：
合法 PDF 用换行，扁平化的损坏文件用空格（三者挤在同一行）。
"""

from __future__ import annotations


def stream(body: bytes) -> bytes:
    """把一个流对象体包成 ``<< /Length n >> stream ... endstream``。"""
    return b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"


def build(objects: list[bytes], *, trailer_separator: bytes = b"\n") -> bytes:
    """把对象体（1 起编号）拼成 PDF 字节串。"""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset

    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += (
        b"startxref"
        + trailer_separator
        + b"%d" % xref
        + trailer_separator
        + b"%%EOF\n"
    )
    return bytes(out)
