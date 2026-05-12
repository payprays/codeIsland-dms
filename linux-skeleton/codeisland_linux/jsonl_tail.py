from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Protocol


LineEntry = tuple[int, str]


class AnchorSelector(Protocol):
    def __call__(self, entries: list[LineEntry], *, tail_start: int) -> list[LineEntry]:
        ...


def read_jsonl(path: Path, *, errors: str = "replace") -> list[str]:
    return path.read_text(encoding="utf-8", errors=errors).splitlines()


def decode_jsonl_line(raw_line: bytes) -> str:
    return raw_line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")


def read_initial_line_entries(
    path: Path,
    *,
    history_lines: int,
    anchor_entries: AnchorSelector | None = None,
    include_first_nonempty: bool = False,
) -> tuple[list[LineEntry], int, int]:
    entries: list[LineEntry] = []
    if history_lines <= 0:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                entries.append((line_number, decode_jsonl_line(raw_line)))
            offset = handle.tell()
        total_lines = entries[-1][0] if entries else 0
        return entries, offset, total_lines

    first_entry: LineEntry | None = None
    tail: deque[LineEntry] = deque(maxlen=history_lines)
    anchor_scan: deque[LineEntry] = deque(maxlen=max(history_lines * 8, 4096))
    total_lines = 0
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = decode_jsonl_line(raw_line)
            if first_entry is None and line.strip():
                first_entry = (line_number, line)
            tail.append((line_number, line))
            anchor_scan.append((line_number, line))
            total_lines = line_number
        offset = handle.tell()

    selected: dict[int, str] = {}
    if include_first_nonempty and first_entry is not None:
        selected[first_entry[0]] = first_entry[1]
    if tail:
        tail_start = tail[0][0]
        if anchor_entries is not None:
            for line_number, line in anchor_entries(list(anchor_scan), tail_start=tail_start):
                selected[line_number] = line
        for line_number, line in tail:
            selected[line_number] = line
    return sorted(selected.items()), offset, total_lines


def read_jsonl_chunk(path: Path, *, offset: int, pending: str = "") -> tuple[list[str], int, str, bool]:
    size = path.stat().st_size
    reset = offset > size
    if reset:
        offset = 0
        pending = ""

    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read()
        next_offset = handle.tell()

    if not raw:
        return [], next_offset, pending, reset

    text = pending + raw.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        return text.splitlines(), next_offset, "", reset

    lines = text.splitlines()
    next_pending = lines.pop() if lines else text
    return lines, next_offset, next_pending, reset
