"""Command-line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__, presets
from .client import DaemonError, call, is_running


def _fmt(v: float, digits: int) -> str:
    return f"{v:.{digits}f}"


def cmd_list(args) -> int:
    for cat, items in presets.by_category().items():
        if args.category and cat.lower() != args.category.lower():
            continue
        print(f"\n{cat}")
        for p in items:
            print(f"  {p.id:<12} {p.name:<18} {p.description}")
    print()
    return 0


def cmd_info(args) -> int:
    p = presets.PRESETS.get(args.preset)
    if not p:
        print(f"unknown preset {args.preset!r}; see 'arch-voice list'", file=sys.stderr)
        return 1
    print(f"{p.name} ({p.id}) — {p.category}\n  {p.description}\n\nParameters:")
    for prm in p.all_params():
        print(f"  {prm.key:<10} {prm.label:<16} default {_fmt(prm.default, prm.digits)}{prm.unit}"
              f"  range {_fmt(prm.min, prm.digits)}..{_fmt(prm.max, prm.digits)}")
    return 0


def _parse_values(pairs: list[str]) -> dict[str, float]:
    values = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        try:
            values[k.strip()] = float(v)
        except ValueError:
            raise SystemExit(f"value for {k!r} must be a number") from None
    return values


def cmd_apply(args) -> int:
    if args.preset not in presets.PRESETS:
        print(f"unknown preset {args.preset!r}; see 'arch-voice list'", file=sys.stderr)
        return 1
    values = _parse_values(args.values)
    valid = {p.key for p in presets.PRESETS[args.preset].all_params()}
    bad = set(values) - valid
    if bad:
        print(f"unknown parameter(s) {', '.join(sorted(bad))}; "
              f"valid: {', '.join(sorted(valid))}", file=sys.stderr)
        return 1
    call("apply", preset=args.preset, values=values)
    print(f"Applied {presets.PRESETS[args.preset].name}. "
          f"Select 'Arch Voice Mic' as the microphone in your apps.")
    return 0


def cmd_set(args) -> int:
    for k, v in _parse_values(args.values).items():
        how = call("set", key=k, value=v)
        print(f"{k} = {v} ({how})")
    return 0


def cmd_status(args) -> int:
    if not is_running():
        print("Arch Voice is not running.")
        return 3
    st = call("status")
    p = presets.PRESETS.get(st["preset"] or "")
    print(f"Preset:   {p.name if p else '-'} ({st['preset']})")
    if p:
        for prm in p.all_params():
            v = st["values"].get(prm.key, prm.default)
            print(f"  {prm.label:<16} {_fmt(v, prm.digits)}{prm.unit}")
    print(f"Input:    {st['input'] or 'default microphone'}")
    print(f"Monitor:  {'on' if st['monitor'] else 'off'}")
    print(f"Quality:  {st['pitch_quality']}")
    print(f"Mic node: {st['mic_node']}")
    if st.get("error"):
        print(f"Last error: {st['error']}")
    return 0


def cmd_inputs(args) -> int:
    st = call("status")
    for s in call("sources"):
        mark = "*" if s["name"] == st["input"] else " "
        print(f" {mark} {s['name']}\n     {s['description']}")
    if not st["input"]:
        print("\n(using the system default microphone)")
    return 0


def cmd_input(args) -> int:
    name = None if args.name in ("default", "") else args.name
    call("input", name=name)
    print(f"Input set to {name or 'system default'}.")
    return 0


def cmd_monitor(args) -> int:
    call("monitor", on=args.state == "on")
    print(f"Monitor {args.state}." + ("  (use headphones to avoid feedback)" if args.state == "on" else ""))
    return 0


def cmd_quality(args) -> int:
    call("quality", value=args.value)
    print(f"Pitch quality: {args.value}")
    return 0


def cmd_reset(args) -> int:
    call("reset")
    print("Parameters reset to preset defaults.")
    return 0


def cmd_stop(args) -> int:
    if not is_running():
        print("Arch Voice is not running.")
        return 0
    call("quit", autostart=False)
    import time
    deadline = time.monotonic() + 10
    while is_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    print("Stopped; the virtual mic has been removed.")
    return 0


def cmd_daemon(args) -> int:
    from .daemon import run
    return run()


def cmd_dump(args) -> int:
    from . import pwconf
    from .daemon import PITCH_ENGINES
    g = presets.build(args.preset, _parse_values(args.values), PITCH_ENGINES[args.quality])
    sys.stdout.write(pwconf.fx_config(g, 0, presets.PRESETS[args.preset].name, None))
    return 0


def cmd_gui(args) -> int:
    from .gui import main
    return main()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="arch-voice",
        description="Real-time voice changer for PipeWire. Run without arguments for the GUI.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command")

    s = sub.add_parser("list", help="list presets")
    s.add_argument("category", nargs="?")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("info", help="show a preset's parameters")
    s.add_argument("preset")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("apply", help="activate a preset, e.g. 'apply demon pitch=-10'")
    s.add_argument("preset")
    s.add_argument("values", nargs="*", metavar="key=value")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("set", help="tweak the active preset, e.g. 'set pitch=-5'")
    s.add_argument("values", nargs="+", metavar="key=value")
    s.set_defaults(func=cmd_set)

    sub.add_parser("reset", help="reset the active preset's parameters").set_defaults(func=cmd_reset)
    sub.add_parser("bypass", help="switch to the clean preset").set_defaults(
        func=lambda a: cmd_apply(argparse.Namespace(preset="clean", values=[])))
    sub.add_parser("status", help="show what is active").set_defaults(func=cmd_status)
    sub.add_parser("inputs", help="list microphones").set_defaults(func=cmd_inputs)

    s = sub.add_parser("input", help="choose the microphone ('default' to follow the system)")
    s.add_argument("name")
    s.set_defaults(func=cmd_input)

    s = sub.add_parser("monitor", help="hear yourself through your speakers/headphones")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("quality", help="pitch shifter: 'fast' (R2) or 'hq' (R3, more CPU)")
    s.add_argument("value", choices=["fast", "hq"])
    s.set_defaults(func=cmd_quality)

    sub.add_parser("stop", help="stop the voice changer and remove the virtual mic").set_defaults(func=cmd_stop)
    sub.add_parser("daemon", help="run the background service in the foreground").set_defaults(func=cmd_daemon)
    sub.add_parser("gui", help="open the graphical interface").set_defaults(func=cmd_gui)

    s = sub.add_parser("dump", help="print the generated PipeWire config for a preset")
    s.add_argument("preset", choices=sorted(presets.PRESETS))
    s.add_argument("values", nargs="*", metavar="key=value")
    s.add_argument("--quality", choices=["fast", "hq"], default="fast")
    s.set_defaults(func=cmd_dump)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        return cmd_gui(args)
    try:
        return args.func(args)
    except DaemonError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
