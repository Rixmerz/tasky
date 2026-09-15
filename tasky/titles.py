"""Session names as Claude Code shows them.

Claude Code appends ``custom-title`` entries to a session transcript when the
user runs ``/rename``, and ``ai-title`` entries with a generated name. The
dashboard should show the same name, so the server follows each transcript
incrementally: only bytes appended since the last scan are read.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_MARKERS = (b'"custom-title"', b'"ai-title"')
_MAX_TITLE = 200


@dataclass
class _Cursor:
    inode: int
    offset: int
    custom: str | None = None
    generated: str | None = None


def title_from_entry(entry: object) -> tuple[str | None, str | None]:
    """Return ``(custom, generated)`` carried by one transcript entry."""
    if not isinstance(entry, dict):
        return None, None
    kind = entry.get("type")
    if kind == "custom-title":
        return _clean(entry.get("customTitle")), None
    if kind == "ai-title":
        return None, _clean(entry.get("aiTitle"))
    return None, None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:_MAX_TITLE] or None


class TitleWatcher:
    """Remember how far each transcript was read and the latest names seen."""

    def __init__(self) -> None:
        self._cursors: dict[str, _Cursor] = {}

    def title(self, path: str | None) -> str | None:
        """The rename if there is one, else the generated name, else None."""
        if not path or not path.endswith(".jsonl"):
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        cursor = self._cursors.get(path)
        if cursor is None or cursor.inode != st.st_ino or st.st_size < cursor.offset:
            cursor = _Cursor(inode=st.st_ino, offset=0)
            self._cursors[path] = cursor
        if st.st_size > cursor.offset:
            self._scan(path, cursor)
        return cursor.custom or cursor.generated

    def _scan(self, path: str, cursor: _Cursor) -> None:
        try:
            with open(path, "rb") as handle:
                handle.seek(cursor.offset)
                data = handle.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return  # no complete line yet; retry once the writer finishes it
        cursor.offset += end + 1
        for line in data[: end + 1].splitlines():
            if not any(marker in line for marker in _MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            custom, generated = title_from_entry(entry)
            if custom:
                cursor.custom = custom
            if generated:
                cursor.generated = generated
