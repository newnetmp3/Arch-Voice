"""Thin wrappers around the PipeWire command-line tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from .pwconf import BUS_NODE, FX_IN, FX_OUT, MIC_NODE, MONITOR_NODE

OUR_PREFIXES = (MIC_NODE, BUS_NODE, FX_IN, FX_OUT, MONITOR_NODE)


class PipeWireError(RuntimeError):
    pass


def require_tools() -> None:
    missing = [t for t in ("pipewire", "pw-dump", "pw-link", "pw-cli") if not shutil.which(t)]
    if missing:
        raise PipeWireError(f"missing PipeWire tools: {', '.join(missing)} "
                            "(install the 'pipewire' package)")


def _run(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise PipeWireError(f"{args[0]} not found") from e
    except subprocess.TimeoutExpired as e:
        raise PipeWireError(f"{args[0]} timed out") from e


def dump() -> list[dict]:
    res = _run(["pw-dump", "--no-colors"])
    if res.returncode != 0:
        raise PipeWireError(f"pw-dump failed: {res.stderr.strip() or 'is PipeWire running?'}")
    try:
        return json.loads(res.stdout or "[]")
    except json.JSONDecodeError as e:
        raise PipeWireError(f"could not parse pw-dump output: {e}") from e


@dataclass
class NodeInfo:
    id: int
    name: str
    description: str
    media_class: str


@dataclass
class PortInfo:
    id: int
    node_id: int
    name: str
    direction: str
    monitor: bool


def _props(obj: dict) -> dict:
    return (obj.get("info") or {}).get("props") or {}


def nodes(objs: list[dict] | None = None) -> list[NodeInfo]:
    out = []
    for o in objs if objs is not None else dump():
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = _props(o)
        out.append(NodeInfo(o["id"], p.get("node.name", ""),
                            p.get("node.description") or p.get("node.nick") or p.get("node.name", ""),
                            p.get("media.class", "")))
    return out


def ports(objs: list[dict] | None = None) -> list[PortInfo]:
    out = []
    for o in objs if objs is not None else dump():
        if o.get("type") != "PipeWire:Interface:Port":
            continue
        p = _props(o)
        info = o.get("info") or {}
        try:
            node_id = int(p.get("node.id"))
        except (TypeError, ValueError):
            continue
        out.append(PortInfo(o["id"], node_id, p.get("port.name", ""),
                            info.get("direction", ""), bool(p.get("port.monitor", False))))
    return out


def find_node(name: str, objs: list[dict] | None = None) -> NodeInfo | None:
    for n in nodes(objs):
        if n.name == name:
            return n
    return None


def node_ports(node_name: str, direction: str, objs: list[dict] | None = None) -> list[PortInfo]:
    objs = objs if objs is not None else dump()
    node = find_node(node_name, objs)
    if not node:
        return []
    return [p for p in ports(objs)
            if p.node_id == node.id and p.direction == direction and not p.monitor]


def is_ours(name: str) -> bool:
    return name.startswith(OUR_PREFIXES)


def sources(objs: list[dict] | None = None) -> list[NodeInfo]:
    """Capture devices the user could pick as input (excluding our own)."""
    return [n for n in nodes(objs)
            if n.media_class.startswith("Audio/Source") and not is_ours(n.name)]


def default_source_name(objs: list[dict] | None = None) -> str | None:
    for o in objs if objs is not None else dump():
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        if _props(o).get("metadata.name") != "default":
            continue
        for entry in o.get("metadata") or []:
            if entry.get("key") == "default.audio.source":
                val = entry.get("value")
                if isinstance(val, dict):
                    return val.get("name")
                if isinstance(val, str):
                    try:
                        return json.loads(val).get("name")
                    except (json.JSONDecodeError, AttributeError):
                        return val
    return None


def link(out_port: int, in_port: int, passive: bool = True) -> None:
    args = ["pw-link"]
    if passive:
        args.append("--passive")
    res = _run(args + [str(out_port), str(in_port)])
    if res.returncode != 0 and "File exists" not in res.stderr:
        if passive:  # older pw-link without --passive
            return link(out_port, in_port, passive=False)
        raise PipeWireError(f"pw-link failed: {res.stderr.strip()}")


def set_controls(node_id: int, controls: dict[str, float]) -> None:
    """Change filter-chain control ports live."""
    if not controls:
        return
    items = " ".join(f'"{k}" {float(v)!r}' for k, v in controls.items())
    res = _run(["pw-cli", "set-param", str(node_id), "Props", f"{{ params = [ {items} ] }}"])
    if res.returncode != 0 or "Error" in res.stderr:
        raise PipeWireError(f"pw-cli set-param failed: {res.stderr.strip()}")


def ladspa_available(plugin: str) -> bool:
    paths = os.environ.get("LADSPA_PATH", "").split(":") + [
        "/usr/lib/ladspa", "/usr/lib64/ladspa", "/usr/local/lib/ladspa",
        "/usr/lib/x86_64-linux-gnu/ladspa", "/usr/lib/aarch64-linux-gnu/ladspa"]
    name = plugin if plugin.endswith(".so") else plugin + ".so"
    return any(p and os.path.exists(os.path.join(p, name)) for p in paths)
