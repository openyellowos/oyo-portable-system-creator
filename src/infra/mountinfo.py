from __future__ import annotations

import os
import re
from pathlib import Path


_MOUNT_ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def _decode_mount_field(value: str) -> str:
    # /proc/self/mountinfo uses octal escapes for ASCII whitespace and backslash.
    # Python's str.split() would also split valid Unicode whitespace (e.g. NBSP).
    if re.search(r"\\(?!040|011|012|134)", value):
        raise RuntimeError("invalid escape in /proc/self/mountinfo")
    return re.sub(
        r"\\(040|011|012|134)",
        lambda m: _MOUNT_ESCAPES[m.group(1)],
        value,
    )


def read_mountinfo_entries() -> list[tuple[str, str]]:
    """Parse the process mount namespace with a single strict ASCII tokenizer.

    Return (mountpoint, source) pairs in kernel order, including overmounts.
    Failure to obtain a complete snapshot must not be interpreted as unmounted.
    """
    snapshot = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    if not snapshot:
        raise RuntimeError("/proc/self/mountinfo is empty")
    lines = snapshot.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or any(not line for line in lines):
        raise RuntimeError("malformed /proc/self/mountinfo snapshot")

    entries: list[tuple[str, str]] = []
    for line in lines:
        fields = line.split(" ")
        if "" in fields:
            raise RuntimeError("malformed /proc/self/mountinfo entry")
        try:
            separator = fields.index("-", 6)
        except ValueError as exc:
            raise RuntimeError("malformed /proc/self/mountinfo entry") from exc
        if len(fields) < 10 or separator + 3 >= len(fields):
            raise RuntimeError("malformed /proc/self/mountinfo entry")
        if (
            not re.fullmatch(r"[0-9]+", fields[0])
            or not re.fullmatch(r"[0-9]+", fields[1])
            or not re.fullmatch(r"[0-9]+:[0-9]+", fields[2])
            or not fields[3].startswith("/")
            or not fields[4].startswith("/")
        ):
            raise RuntimeError("invalid required field in /proc/self/mountinfo entry")
        mountpoint = os.path.normpath(_decode_mount_field(fields[4]))
        source = _decode_mount_field(fields[separator + 2])
        entries.append((mountpoint, source))
    return entries
