"""归一化提取文本中的代理项（surrogate）码位。

pypdf 用 ``errors="surrogatepass"`` 解码损坏的 ``ToUnicode`` CMap，畸形映射
会产出孤立代理项而不是抛错。代理项无法编码为 UTF-8，于是下游的
``hashlib.sha256(text.encode())`` 抛 ``UnicodeEncodeError``，上传接口再把它
（因为 UnicodeEncodeError 是 ValueError 的子类）误报成"文件已损坏"。

替换必须逐字符进行：``encode("utf-8", "replace")`` 得到的是字面量 ``?`` 而非
U+FFFD；``surrogatepass`` 往返则会把一个坏字符变成三个，后面所有偏移都跟着
移动。逐字符替换保证长度不变、其余字符位置不变。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

REPLACEMENT_CHARACTER = "\ufffd"  # U+FFFD

_SURROGATE_START = 0xD800
_SURROGATE_END = 0xDFFF


@dataclass(frozen=True)
class SanitizedText:
    """归一化结果：文本本身，以及被替换字符的下标。"""

    text: str
    positions: tuple[int, ...]

    @property
    def replacements(self) -> int:
        return len(self.positions)


def sanitize_surrogates(text: str) -> SanitizedText:
    """把文本里每个代理项码位替换为 U+FFFD。

    长度与其余字符的位置一律不变。没有代理项时原样返回，不做拷贝。
    """
    if not any(_SURROGATE_START <= ord(char) <= _SURROGATE_END for char in text):
        return SanitizedText(text=text, positions=())
    chars = list(text)
    positions: list[int] = []
    for index, char in enumerate(chars):
        if _SURROGATE_START <= ord(char) <= _SURROGATE_END:
            chars[index] = REPLACEMENT_CHARACTER
            positions.append(index)
    return SanitizedText(text="".join(chars), positions=tuple(positions))


def format_positions(positions: tuple[int, ...], limit: int = 50) -> str:
    """把替换位置排成一行日志文本；替换总数始终完整给出。"""
    shown = ", ".join(str(position) for position in positions[:limit])
    if len(positions) > limit:
        return f"{shown}, ...(共 {len(positions)} 处)"
    return shown


def sha256_of_text(text: str) -> str:
    """文本内容哈希，编码前先做归一化，使哈希永远不会抛 UnicodeEncodeError。

    registry 今天已是唯一入口，这里仍逐站兜底：不变量不该依赖这一点长期成立。
    """
    return hashlib.sha256(sanitize_surrogates(text).text.encode()).hexdigest()
