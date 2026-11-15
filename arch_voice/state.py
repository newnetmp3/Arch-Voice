"""Persistent settings and standard paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or os.path.expanduser(fallback))


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", "~/.config") / "arch-voice"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", "~/.cache") / "arch-voice"


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/arch-voice-{os.getuid()}"
    Path(runtime).mkdir(mode=0o700, exist_ok=True)
    return Path(runtime) / "arch-voice.sock"


@dataclass
class State:
    preset: str = "clean"
    input: str | None = None
    monitor: bool = False
    pitch_quality: str = "fast"
    # Per-preset parameter overrides: {preset_id: {param: value}}
    params: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "state.json"

    @classmethod
    def load(cls) -> "State":
        try:
            data = json.loads(cls.path().read_text())
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        state = cls(**known)
        from . import presets
        if state.preset not in presets.PRESETS:
            state.preset = "clean"
        return state

    def save(self) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)

    def values_for(self, preset_id: str) -> dict[str, float]:
        from . import presets
        return {**presets.PRESETS[preset_id].defaults(), **self.params.get(preset_id, {})}

    def set_values(self, preset_id: str, values: dict[str, float]) -> None:
        from . import presets
        defaults = presets.PRESETS[preset_id].defaults()
        changed = {k: v for k, v in values.items() if defaults.get(k) != v}
        if changed:
            self.params[preset_id] = changed
        else:
            self.params.pop(preset_id, None)
