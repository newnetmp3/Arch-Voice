"""Minimal serializer for PipeWire's SPA-JSON configuration format.

SPA-JSON is a relaxed superset of JSON. We emit strict-ish JSON with
quoted strings everywhere, which PipeWire parses fine, but we keep the
output indented and readable so generated configs are easy to debug.
"""

from __future__ import annotations

import math
from typing import Any


class Raw(str):
    """A value emitted verbatim (no quoting), e.g. ``MONO`` or ``true``."""


def _scalar(value: Any) -> str:
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"cannot serialize non-finite float {value!r}")
        text = repr(float(value))
        return text
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"cannot serialize {type(value).__name__}")


def dumps(value: Any, indent: int = 0, step: int = 4) -> str:
    pad = " " * indent
    inner = " " * (indent + step)
    if isinstance(value, dict):
        if not value:
            return "{ }"
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{inner}{_key(key)} = {dumps(item, indent + step, step)}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[ ]"
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            return "[ " + " ".join(_scalar(v) for v in value) + " ]"
        lines = ["["]
        for item in value:
            lines.append(f"{inner}{dumps(item, indent + step, step)}")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return _scalar(value)


def _key(key: str) -> str:
    # Bare keys are allowed when they only contain "safe" characters.
    if key and all(c.isalnum() or c in "._-*" for c in key):
        return key
    return _scalar(key)


def dump_document(sections: dict[str, Any]) -> str:
    """Serialize a top-level config file (sections without outer braces)."""
    parts = []
    for key, value in sections.items():
        parts.append(f"{key} = {dumps(value)}")
    return "\n\n".join(parts) + "\n"
