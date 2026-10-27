"""Filesystem-safe, deterministic names for generated documentation files."""

from __future__ import annotations

import re
from urllib.parse import unquote

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def decode_tmdl_filename(stem: str) -> str:
    """Decode the URL-encoding TMDL applies to object names in file names."""
    return unquote(stem)


def safe_stem(name: str) -> str:
    stem = _UNSAFE_RE.sub("_", name).strip(" .")
    if not stem:
        stem = "_"
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return stem


def assign_filenames(names: list[str], extension: str = ".md") -> dict[str, str]:
    """Map each name to a unique safe filename.

    Uniqueness is case-insensitive so output is portable to Windows and macOS.
    Names are processed in sorted order so suffixes are stable across runs.
    """
    used: set[str] = set()
    result: dict[str, str] = {}
    for name in sorted(set(names), key=lambda n: (n.casefold(), n)):
        stem = safe_stem(name)
        candidate = stem
        counter = 2
        while candidate.casefold() in used:
            candidate = f"{stem}-{counter}"
            counter += 1
        used.add(candidate.casefold())
        result[name] = candidate + extension
    return result
