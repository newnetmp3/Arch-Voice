"""The Arch Voice background service.

Owns three kinds of child processes:

* the *mic host*: a PipeWire loopback exposing the ``arch_voice_mic``
  virtual microphone. It stays up for the whole session.
* the *FX chain*: a filter-chain for the active preset. On preset change a
  new chain is started and linked before the old one is stopped, so the
  switch is near-gapless and apps never lose the virtual mic.
* an optional *monitor* loopback that plays the processed voice to your
  speakers/headphones so you can hear yourself.

Clients (CLI and GUI) talk to it via a JSON-lines Unix socket.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import presets, pwconf, pwutil
from .dsp import Graph, PitchEngine
from .state import State, cache_dir, socket_path

PITCH_ENGINES = {
    "fast": PitchEngine(label="rubberband-pitchshifter-mono"),
    "hq": PitchEngine(label="rubberband-r3-pitchshifter-mono"),
}


def log(msg: str) -> None:
    print(f"[arch-voice] {msg}", file=sys.stderr, flush=True)


class Child:
    """A ``pipewire -c <conf>`` (or other) child process with a log file."""

    def __init__(self, name: str, args: list[str]):
        self.name = name
        logdir = cache_dir() / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        self.logfile = open(logdir / f"{name}.log", "w")
        self.proc = subprocess.Popen(args, stdout=self.logfile, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 2.0) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.logfile.close()

    def tail(self, lines: int = 8) -> str:
        try:
            self.logfile.flush()
            text = Path(self.logfile.name).read_text(errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])


@dataclass
class Chain:
    child: Child
    generation: int
    preset: str
    values: dict[str, float]
    graph: Graph


class Engine:
    def __init__(self):
        self.state = State.load()
        self.lock = threading.RLock()
        self.mic: Child | None = None
        self.chain: Chain | None = None
        self.monitor: Child | None = None
        self.generation = 0
        self.last_error: str | None = None
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        pwutil.require_tools()
        with self.lock:
            self._start_mic()
            try:
                self.apply(self.state.preset, self.state.values_for(self.state.preset))
            except Exception as e:  # keep running with the clean preset
                self.last_error = str(e)
                log(f"could not restore preset {self.state.preset!r}: {e}")
                self.apply("clean", {})
            # Never re-enable monitoring on its own: hearing yourself out of
            # the blue at login is unpleasant (and can feed back on speakers).
            self.state.monitor = False
            self.state.save()

    def shutdown(self) -> None:
        with self.lock:
            self._stopping = True
            for child in (self.monitor, self.chain.child if self.chain else None, self.mic):
                if child:
                    child.stop()
            self.monitor = self.chain = self.mic = None

    def _start_mic(self) -> None:
        if self.mic and self.mic.alive():
            return
        existing = pwutil.find_node(pwconf.MIC_NODE)
        if existing:
            raise pwutil.PipeWireError(
                "another Arch Voice instance already provides the virtual mic")
        path = cache_dir() / "mic.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pwconf.mic_config())
        self.mic = Child("mic", ["pipewire", "-c", str(path)])
        self._wait_for(lambda objs: pwutil.node_ports(pwconf.BUS_NODE, "input", objs),
                       "virtual mic", self.mic)

    def _wait_for(self, check, what: str, child: Child, timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not child.alive():
                raise pwutil.PipeWireError(f"{what} process exited:\n{child.tail()}")
            result = check(pwutil.dump())
            if result:
                return result
            time.sleep(0.1)
        raise pwutil.PipeWireError(f"timed out waiting for {what}:\n{child.tail()}")

    # -- presets -----------------------------------------------------------

    def _graph(self, preset_id: str, values: dict[str, float]) -> Graph:
        engine = PITCH_ENGINES.get(self.state.pitch_quality, PITCH_ENGINES["fast"])
        return presets.build(preset_id, values, engine)

    def _resolve_input(self) -> str | None:
        """Pick the capture device, never our own virtual mic (feedback!)."""
        objs = pwutil.dump()
        names = {n.name for n in pwutil.sources(objs)}
        if self.state.input and self.state.input in names:
            return self.state.input
        default = pwutil.default_source_name(objs)
        if default and not pwutil.is_ours(default):
            return default  # pin it explicitly so we never follow ourselves
        hw = [n for n in pwutil.sources(objs) if n.media_class == "Audio/Source"]
        return hw[0].name if hw else None

    def apply(self, preset_id: str, values: dict[str, float] | None = None) -> None:
        if preset_id not in presets.PRESETS:
            raise KeyError(f"unknown preset {preset_id!r}")
        with self.lock:
            self._start_mic()
            values = dict(values if values is not None else self.state.values_for(preset_id))
            graph = self._graph(preset_id, values)
            for node in graph.nodes:
                if node.type == "ladspa" and not pwutil.ladspa_available(node.plugin):
                    raise pwutil.PipeWireError(
                        f"preset needs the LADSPA plugin '{node.plugin}' "
                        "(on Arch: sudo pacman -S rubberband-ladspa)")

            self.generation += 1
            gen = self.generation
            title = presets.PRESETS[preset_id].name
            conf = cache_dir() / f"fx-{gen}.conf"
            conf.write_text(pwconf.fx_config(graph, gen, title, self._resolve_input()))
            child = Child(f"fx-{gen}", ["pipewire", "-c", str(conf)])
            try:
                self._link_chain(gen, child)
            except Exception:
                child.stop()
                conf.unlink(missing_ok=True)
                raise

            old, self.chain = self.chain, Chain(child, gen, preset_id, values, graph)
            if old:
                old.child.stop()
                (cache_dir() / f"fx-{old.generation}.conf").unlink(missing_ok=True)
            self.state.preset = preset_id
            self.state.set_values(preset_id, values)
            self.state.save()
            self.last_error = None
            log(f"applied preset {preset_id} (generation {gen})")

    def _link_chain(self, gen: int, child: Child) -> None:
        out_name = f"{pwconf.FX_OUT}.{gen}"

        def ready(objs):
            outs = pwutil.node_ports(out_name, "output", objs)
            ins = pwutil.node_ports(pwconf.BUS_NODE, "input", objs)
            return (outs, ins) if outs and ins else None

        outs, ins = self._wait_for(ready, "effect chain", child)
        for o, i in zip(outs, ins):
            pwutil.link(o.id, i.id)

    def set_param(self, key: str, value: float) -> str:
        """Update one parameter of the active preset. Returns 'live' or 'restart'."""
        with self.lock:
            if not self.chain:
                raise RuntimeError("no active preset")
            pr = presets.PRESETS[self.chain.preset]
            spec = {p.key: p for p in pr.all_params()}.get(key)
            if not spec:
                raise KeyError(f"preset {pr.id!r} has no parameter {key!r}")
            value = min(spec.max, max(spec.min, float(value)))
            values = {**self.chain.values, key: value}
            new_graph = self._graph(pr.id, values)
            bindings = [b for b in new_graph.bindings if b.key == key]

            if bindings and _same_structure(self.chain.graph, new_graph):
                objs = pwutil.dump()
                gen = self.chain.generation
                # Depending on the PipeWire version the graph's Props live on
                # the capture or playback stream; setting both is harmless.
                targets = [n for n in (pwutil.find_node(f"{pwconf.FX_IN}.{gen}", objs),
                                       pwutil.find_node(f"{pwconf.FX_OUT}.{gen}", objs)) if n]
                if targets:
                    try:
                        controls = {f"{b.node}:{b.port}": b.fn(value) for b in bindings}
                        for node in targets:
                            pwutil.set_controls(node.id, controls)
                        self.chain.values = values
                        self.chain.graph = new_graph
                        self.state.set_values(pr.id, values)
                        self.state.save()
                        return "live"
                    except pwutil.PipeWireError as e:
                        log(f"live update failed, restarting chain: {e}")
            self.apply(pr.id, values)
            return "restart"

    def reset_params(self) -> None:
        with self.lock:
            if self.chain:
                self.apply(self.chain.preset, presets.PRESETS[self.chain.preset].defaults())

    # -- input / monitor ---------------------------------------------------

    def set_input(self, name: str | None) -> None:
        with self.lock:
            self.state.input = name or None
            self.state.save()
            if self.chain:
                self.apply(self.chain.preset, self.chain.values)

    def set_monitor(self, on: bool) -> None:
        """Play the processed voice to the default output so you can hear it.

        Like the FX chain, the monitor is a loopback we link to the virtual
        mic ourselves with pw-link rather than relying on the session
        manager to find it by name (which WirePlumber 0.5 may not do for a
        source that is itself a loopback stream).
        """
        with self.lock:
            if self.monitor:
                self.monitor.stop()
                self.monitor = None
            self.state.monitor = False
            if on:
                self._start_mic()
                path = cache_dir() / "monitor.conf"
                path.write_text(pwconf.monitor_config())
                child = Child("monitor", ["pipewire", "-c", str(path)])

                def ready(objs):
                    outs = pwutil.node_ports(pwconf.MIC_NODE, "output", objs)
                    ins = pwutil.node_ports(pwconf.MONITOR_IN, "input", objs)
                    return (outs, ins) if outs and ins else None

                try:
                    outs, ins = self._wait_for(ready, "monitor", child)
                    for o, i in zip(outs, ins):
                        # An active link: monitoring should keep the mic running.
                        pwutil.link(o.id, i.id, passive=False)
                except Exception as e:
                    child.stop()
                    self.state.save()
                    raise pwutil.PipeWireError(f"could not start monitoring: {e}") from e
                self.monitor = child
                self.state.monitor = True
            self.state.save()

    # -- status / supervision ----------------------------------------------

    def status(self) -> dict:
        with self.lock:
            chain = self.chain
            return {
                "running": bool(self.mic and self.mic.alive()),
                "preset": chain.preset if chain else None,
                "values": chain.values if chain else {},
                "input": self.state.input,
                "monitor": bool(self.monitor and self.monitor.alive()),
                "pitch_quality": self.state.pitch_quality,
                "mic_node": pwconf.MIC_NODE,
                "error": self.last_error,
            }

    def watchdog(self) -> None:
        """Restart children that died (e.g. PipeWire restarted)."""
        while not self._stopping:
            time.sleep(2.0)
            with self.lock:
                if self._stopping:
                    return
                try:
                    if self.mic and not self.mic.alive():
                        log("virtual mic process died; restarting")
                        self.mic.stop()
                        self.mic = None
                        self._start_mic()
                        if self.chain:
                            self.chain.child.stop()
                            self.chain = None
                        self.apply(self.state.preset)
                        if self.monitor:  # its link to the old mic is gone
                            self.set_monitor(True)
                    elif self.chain and not self.chain.child.alive():
                        log("effect chain died; restarting")
                        self.chain.child.stop()
                        preset, values = self.chain.preset, self.chain.values
                        self.chain = None
                        self.apply(preset, values)
                    if self.monitor and not self.monitor.alive():
                        log("monitor died; restarting")
                        self.set_monitor(True)
                except Exception as e:  # keep the watchdog alive
                    self.last_error = str(e)
                    log(f"watchdog: {e}")


def _same_structure(a: Graph, b: Graph) -> bool:
    """True if graphs differ only in values of bound (live) control ports."""
    def masked(g: Graph):
        bound = {(x.node, x.port) for x in g.bindings}
        spa = g.to_spa()
        for n in spa["nodes"]:
            for port in list(n.get("control", {})):
                if (n["name"], port) in bound:
                    n["control"][port] = 0.0
        return spa
    return masked(a) == masked(b)


# --------------------------------------------------------------------------
# Socket server


class _Handler(socketserver.StreamRequestHandler):
    engine: Engine
    server_ref: "Server"

    def handle(self):
        for line in self.rfile:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
                result = self.dispatch(req)
                reply = {"ok": True, "result": result}
            except Exception as e:  # report every error to the client
                reply = {"ok": False, "error": str(e)}
            self.wfile.write((json.dumps(reply) + "\n").encode())
            self.wfile.flush()

    def dispatch(self, req: dict):
        eng = self.engine
        cmd = req.get("cmd")
        if cmd == "status":
            return eng.status()
        if cmd == "apply":
            preset_id = req["preset"]
            values = {**eng.state.values_for(preset_id), **req.get("values", {})}
            eng.apply(preset_id, values)
            return eng.status()
        if cmd == "set":
            return eng.set_param(req["key"], req["value"])
        if cmd == "reset":
            eng.reset_params()
            return eng.status()
        if cmd == "input":
            eng.set_input(req.get("name"))
            return eng.status()
        if cmd == "monitor":
            eng.set_monitor(bool(req["on"]))
            return eng.status()
        if cmd == "quality":
            if req["value"] not in PITCH_ENGINES:
                raise ValueError("quality must be 'fast' or 'hq'")
            eng.state.pitch_quality = req["value"]
            eng.state.save()
            if eng.chain:
                eng.apply(eng.chain.preset, eng.chain.values)
            return eng.status()
        if cmd == "sources":
            objs = pwutil.dump()
            return [{"name": n.name, "description": n.description}
                    for n in pwutil.sources(objs)]
        if cmd == "quit":
            threading.Thread(target=self.server_ref.stop, daemon=True).start()
            return "bye"
        raise ValueError(f"unknown command {cmd!r}")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, engine: Engine):
        path = socket_path()
        if path.exists():
            try:  # is another daemon alive?
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(str(path))
                raise SystemExit("arch-voice daemon is already running")
            except ConnectionRefusedError:
                path.unlink()
        handler = type("Handler", (_Handler,), {"engine": engine, "server_ref": self})
        super().__init__(str(path), handler)
        os.chmod(path, 0o600)
        self.engine = engine
        self.path = path

    def stop(self):
        self.engine.shutdown()
        self.shutdown()


def run() -> int:
    engine = Engine()
    server = Server(engine)
    try:
        engine.start()
    except Exception as e:
        log(f"failed to start: {e}")
        engine.shutdown()
        server.server_close()
        server.path.unlink(missing_ok=True)
        return 1

    def on_signal(signum, frame):
        threading.Thread(target=server.stop, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    threading.Thread(target=engine.watchdog, daemon=True).start()
    log(f"ready; virtual mic '{pwconf.MIC_NODE}', socket {server.path}")
    try:
        server.serve_forever()
    finally:
        engine.shutdown()
        server.server_close()
        server.path.unlink(missing_ok=True)
    return 0
