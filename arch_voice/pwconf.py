"""Generate standalone PipeWire client configs (run with ``pipewire -c``)."""

from __future__ import annotations

from .dsp import Graph
from .spajson import Raw, dump_document

MIC_NODE = "arch_voice_mic"      # the virtual microphone apps record from
BUS_NODE = "arch_voice_bus"      # internal input that the FX chain feeds
FX_IN = "arch_voice_fx_in"       # FX chain capture stream (prefix)
FX_OUT = "arch_voice_fx_out"     # FX chain playback stream (prefix)
MONITOR_NODE = "arch_voice_monitor"   # monitor playback (to your speakers)
MONITOR_IN = "arch_voice_monitor_in"  # monitor capture (linked from the mic)

_BASE = {
    "context.properties": {"log.level": 0},
    "context.spa-libs": {
        "audio.convert.*": "audioconvert/libspa-audioconvert",
        "support.*": "support/libspa-support",
    },
}


def _modules(extra: list[dict]) -> list[dict]:
    return [
        {"name": "libpipewire-module-rt", "args": {"nice.level": -11},
         "flags": [Raw("ifexists"), Raw("nofail")]},
        {"name": "libpipewire-module-protocol-native"},
        {"name": "libpipewire-module-client-node"},
        {"name": "libpipewire-module-adapter"},
        *extra,
    ]


def mic_config(description: str = "Arch Voice Mic") -> str:
    """A loopback whose output side is an Audio/Source: the virtual mic.

    This process lives for the whole session so apps stay connected to the
    same node while the FX chain behind it is swapped between presets.
    """
    loopback = {
        "name": "libpipewire-module-loopback",
        "args": {
            "node.description": description,
            "audio.channels": 1,
            "audio.position": [Raw("MONO")],
            "capture.props": {
                "node.name": BUS_NODE,
                "node.description": "Arch Voice (internal bus)",
                "node.autoconnect": False,
                "audio.channels": 1,
                "audio.position": [Raw("MONO")],
                "media.role": "Communication",
            },
            "playback.props": {
                "node.name": MIC_NODE,
                "node.description": description,
                "media.class": "Audio/Source",
                "audio.channels": 1,
                "audio.position": [Raw("MONO")],
                "device.icon-name": "audio-input-microphone",
            },
        },
    }
    return dump_document({**_BASE, "context.modules": _modules([loopback])})


def fx_config(graph: Graph, generation: int, title: str,
              input_target: str | None) -> str:
    capture = {
        "node.name": f"{FX_IN}.{generation}",
        "node.description": f"Arch Voice FX input ({title})",
        # Passive only towards the hardware mic, as in PipeWire's own
        # virtual-source examples: the mic is opened when someone listens.
        "node.passive": True,
        "node.dont-reconnect": False,
        "audio.channels": 1,
        "audio.position": [Raw("MONO")],
        "media.role": "Communication",
    }
    if input_target:
        capture["target.object"] = input_target
    chain = {
        "name": "libpipewire-module-filter-chain",
        "args": {
            "node.description": f"Arch Voice FX: {title}",
            "media.name": f"Arch Voice FX: {title}",
            "audio.channels": 1,
            "audio.position": [Raw("MONO")],
            "filter.graph": graph.to_spa(),
            "capture.props": capture,
            "playback.props": {
                "node.name": f"{FX_OUT}.{generation}",
                "node.description": f"Arch Voice FX output ({title})",
                "node.autoconnect": False,
                "audio.channels": 1,
                "audio.position": [Raw("MONO")],
            },
        },
    }
    return dump_document({**_BASE, "context.modules": _modules([chain])})


def monitor_config() -> str:
    """Loopback from the virtual mic to the default output device."""
    loopback = {
        "name": "libpipewire-module-loopback",
        "args": {
            "node.description": "Arch Voice monitor",
            "audio.channels": 1,
            "audio.position": [Raw("MONO")],
            "capture.props": {
                "node.name": MONITOR_IN,
                "node.description": "Arch Voice monitor (input)",
                "node.autoconnect": False,  # we link it to the mic ourselves
                "stream.dont-remix": True,
            },
            "playback.props": {
                "node.name": MONITOR_NODE,
                "node.description": "Arch Voice monitor",
                # Upmix mono to both ears instead of only the left channel.
                "audio.position": [Raw("MONO")],
            },
        },
    }
    return dump_document({**_BASE, "context.modules": _modules([loopback])})
