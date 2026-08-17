from __future__ import annotations

from collections.abc import Iterator
import lzma
from pathlib import Path
from typing import TextIO

ENTRY_START = "<entry"
ENTRY_END = "</entry>"


def _open_text(path: Path) -> TextIO:
    if path.suffix == ".xz":
        return lzma.open(path, "rt", encoding="utf-8", newline="")
    return path.open("rt", encoding="utf-8", newline="")


def iter_entry_xml(source: str | Path, chunk_size: int = 1024 * 1024) -> Iterator[str]:
    path = Path(source)
    buffer = ""
    inside = False
    with _open_text(path) as handle:
        while True:
            chunk = handle.read(chunk_size)
            eof = chunk == ""
            buffer += chunk
            while True:
                if not inside:
                    start = _find_entry_start(buffer)
                    if start < 0:
                        buffer = "" if eof else buffer[-len(ENTRY_START) :]
                        break
                    buffer = buffer[start:]
                    inside = True
                end = buffer.find(ENTRY_END)
                if end < 0:
                    break
                end += len(ENTRY_END)
                yield buffer[:end]
                buffer = buffer[end:]
                inside = False
            if eof:
                break
    if inside:
        raise ValueError("O XML terminou com uma entrada incompleta.")


def _find_entry_start(buffer: str) -> int:
    position = 0
    while True:
        position = buffer.find(ENTRY_START, position)
        if position < 0:
            return -1
        after = position + len(ENTRY_START)
        if after == len(buffer) or buffer[after].isspace() or buffer[after] == ">":
            return position
        position = after

