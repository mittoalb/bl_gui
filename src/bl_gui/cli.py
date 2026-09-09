"""``bl-cli`` — argparse-driven CLI for bl_gui's headless surface.

Every subcommand maps to one function in ``bl_gui.headless.*``. Human-
readable text output by default; ``--json`` on any subcommand emits a
machine-readable JSON payload for agents / scripts. Nonzero exit code
on any failure so shell chains do the right thing.

Run ``bl-cli --help`` for the full subcommand tree."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import headless as h


# ── shared helpers ───────────────────────────────────────────────────

def _emit(payload: Any, as_json: bool, human_writer=None) -> None:
    """If ``as_json``, dump the payload as pretty JSON. Otherwise call
    ``human_writer(payload)`` (falls back to ``print(payload)``)."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if human_writer is not None:
        human_writer(payload)
    else:
        print(payload)


# ── `layout` subcommands ─────────────────────────────────────────────

def _cmd_layout_list(args) -> int:
    lay = h.load_layout(args.name)
    if args.json:
        _emit(lay, True); return 0
    print(f"layout: {lay['name']}  ({lay['path']})")
    print(f"  tabs: {', '.join(lay['tabs'])}")
    print(f"  panels: {len(lay['panels'])}  "
          f"(deleted: {len(lay['deleted_panels'])})")
    return 0


def _cmd_layout_motors(args) -> int:
    ms = h.list_motors(h.load_layout(args.name))
    if args.json:
        _emit(ms, True); return 0
    if not ms:
        print("(no motors defined in this layout)")
        return 0
    w = max(len(m["label"]) for m in ms)
    for m in ms:
        print(f"  {m['label']:<{w}}  {m['pv']:<32}  "
              f"[{m['panel_title']} / {m['tab']}]")
    return 0


def _cmd_layout_panels(args) -> int:
    ps = h.list_panels(h.load_layout(args.name))
    if args.json:
        _emit(ps, True); return 0
    if not ps:
        print("(no panels in this layout)")
        return 0
    w = max(len(p["title"]) for p in ps)
    for p in ps:
        print(f"  {p['title']:<{w}}  ({p['tab']})  "
              f"motors={p['motor_count']:>2}  "
              f"buttons={p['button_count']:>2}  "
              f"pv_fields={p['pv_field_count']:>2}")
    return 0


def _cmd_layout_actions(args) -> int:
    acts = h.list_actions(h.load_layout(args.name))
    if args.json:
        _emit(acts, True); return 0
    if not acts:
        print("(no saved CfgButtons in this layout — defaults are "
              "constructed in the GUI at build time)")
        return 0
    for a in acts:
        print(f"  [{a['type']}] {a['label']}  →  {a['action']}   "
              f"({a['panel_key']})")
    return 0


# ── `motor` subcommands ──────────────────────────────────────────────

def _cmd_motor_get(args) -> int:
    v = h.caget(args.pv, timeout=args.timeout)
    if v is None:
        print(f"caget failed: {args.pv}", file=sys.stderr)
        return 1
    _emit({"pv": args.pv, "value": v}, args.json,
          human_writer=lambda p: print(p["value"]))
    return 0


def _cmd_motor_rbv(args) -> int:
    v = h.caget_rbv(args.motor_pv, timeout=args.timeout)
    if v is None:
        print(f"RBV read failed: {args.motor_pv}", file=sys.stderr)
        return 1
    _emit({"motor": args.motor_pv, "rbv": v}, args.json,
          human_writer=lambda p: print(f"{p['rbv']:.6f}"))
    return 0


def _cmd_motor_set(args) -> int:
    ok = h.caput(args.pv, args.value, timeout=args.timeout)
    _emit({"pv": args.pv, "value": args.value, "ok": ok}, args.json,
          human_writer=lambda p: print("ok" if p["ok"] else "FAILED"))
    return 0 if ok else 1


def _cmd_motor_wait(args) -> int:
    ok = h.wait_dmov(args.motor_pv, timeout=args.timeout)
    _emit({"motor": args.motor_pv, "settled": ok}, args.json,
          human_writer=lambda p: print("settled" if p["settled"] else "TIMEOUT"))
    return 0 if ok else 1


# ── `energy` subcommands ─────────────────────────────────────────────

def _cmd_energy_interp(args) -> int:
    rows = h.interp_at_energy(args.keV,
                               respect_regime=not args.no_regime)
    if args.json:
        _emit(rows, True); return 0
    for r in rows:
        t = "-"    if r["target"] is None else f"{r['target']:.6f}"
        note = f"  [{r['reason']}]" if r["reason"] else ""
        print(f"  {r['label']:<20} {r['pv']:<32} -> {t:<12} "
              f"{r['status']}{note}")
    return 0


def _cmd_energy_set(args) -> int:
    rows = h.move_motors_to_energy(args.keV,
                                    dry_run=args.dry_run,
                                    respect_regime=not args.no_regime)
    if args.json:
        _emit(rows, True); return 0
    for r in rows:
        t = "-"    if r["target"] is None else f"{r['target']:.6f}"
        note = f"  [{r['reason']}]" if r["reason"] else ""
        print(f"  {r['label']:<20} {r['pv']:<32} -> {t:<12} "
              f"{r['status']}{note}")
    # Nonzero if any caput failed (skipped / dry-run counts as success).
    bad = [r for r in rows if r["status"] in ("caput_failed", "error")]
    return 1 if bad else 0


# ── `qgmax` subcommands ──────────────────────────────────────────────

def _cmd_qgmax_trigger(args) -> int:
    ts = h.qgmax.trigger()
    _emit({"ts": ts, "file": h.qgmax.REQUEST_FILE}, args.json,
          human_writer=lambda p: print(f"triggered at ts={p['ts']:.3f}"))
    return 0


def _cmd_qgmax_status(args) -> int:
    st = h.qgmax.read_status()
    if st is None:
        _emit({"status": None}, args.json,
              human_writer=lambda p: print("(no response file yet)"))
        return 0
    _emit(st, args.json,
          human_writer=lambda p: print(
              f"running={p['running']}  "
              f"started_ts={p['started_ts']}  "
              f"completed_ts={p['completed_ts']}"))
    return 0


# ── `autofocus` — wraps beamlines/bl32id/autofocus.run ───────────────

def _cmd_autofocus(args) -> int:
    rv = h.autofocus.run(motor_pv=args.motor, image_pv=args.image_pv,
                         half_range_mm=args.half_range, steps=args.steps,
                         exposure_s=args.exposure,
                         exposure_pv=args.exposure_pv,
                         acquire_pv=args.acquire_pv,
                         settle_s=args.settle, timeout_s=args.timeout)
    if rv is None:
        print("autofocus failed", file=sys.stderr)
        return 1
    best_pos, results = rv
    _emit({"best_pos": best_pos, "samples": results}, args.json,
          human_writer=lambda p: print(f"best={p['best_pos']:.6f}"))
    return 0


# ── `regime` subcommands ─────────────────────────────────────────────

def _cmd_regime_get(args) -> int:
    r = h.regime.read()
    _emit({"regime": r, "file": h.regime.STATE_FILE}, args.json,
          human_writer=lambda p: print(p["regime"]))
    return 0


def _cmd_regime_set(args) -> int:
    try:
        h.regime.write(args.mode)
    except ValueError as e:
        print(f"regime set failed: {e}", file=sys.stderr)
        return 1
    _emit({"regime": h.regime.read()}, args.json,
          human_writer=lambda p: print(p["regime"]))
    return 0


# ── argparse tree ────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bl-cli",
        description="Headless CLI for bl_gui (motors, layout, energy, "
                    "qgmax, autofocus, regime).")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of text")
    sub = ap.add_subparsers(dest="group", required=True)

    # layout
    lp = sub.add_parser("layout", help="Layout JSON queries").add_subparsers(
        dest="cmd", required=True)
    lp_list = lp.add_parser("list", help="Show layout summary")
    lp_list.add_argument("--name", default=None,
                         help="Layout name (default = whatever the GUI would load)")
    lp_list.set_defaults(func=_cmd_layout_list)
    lp_m = lp.add_parser("motors", help="List every motor in the layout")
    lp_m.add_argument("--name", default=None)
    lp_m.set_defaults(func=_cmd_layout_motors)
    lp_p = lp.add_parser("panels", help="Panel summary")
    lp_p.add_argument("--name", default=None)
    lp_p.set_defaults(func=_cmd_layout_panels)
    lp_a = lp.add_parser("actions", help="Every saved CfgButton")
    lp_a.add_argument("--name", default=None)
    lp_a.set_defaults(func=_cmd_layout_actions)

    # motor
    mp = sub.add_parser("motor", help="EPICS caget/caput/wait on one PV")\
        .add_subparsers(dest="cmd", required=True)
    mp_g = mp.add_parser("get", help="caget -t on a PV")
    mp_g.add_argument("pv"); mp_g.add_argument("--timeout", type=float, default=2.0)
    mp_g.set_defaults(func=_cmd_motor_get)
    mp_r = mp.add_parser("rbv", help="Read <motor>.RBV")
    mp_r.add_argument("motor_pv"); mp_r.add_argument("--timeout", type=float, default=2.0)
    mp_r.set_defaults(func=_cmd_motor_rbv)
    mp_s = mp.add_parser("set", help="caput a value")
    mp_s.add_argument("pv"); mp_s.add_argument("value")
    mp_s.add_argument("--timeout", type=float, default=5.0)
    mp_s.set_defaults(func=_cmd_motor_set)
    mp_w = mp.add_parser("wait", help="Wait until <motor>.DMOV=1")
    mp_w.add_argument("motor_pv"); mp_w.add_argument("--timeout", type=float, default=30.0)
    mp_w.set_defaults(func=_cmd_motor_wait)

    # energy
    ep = sub.add_parser("energy", help="ZP-calibration energy sweep")\
        .add_subparsers(dest="cmd", required=True)
    ep_i = ep.add_parser("interp", help="Compute motor targets at <keV> (no caput)")
    ep_i.add_argument("keV", type=float)
    ep_i.add_argument("--no-regime", action="store_true",
                      help="Ignore nano/micro regime; interp every included motor")
    ep_i.set_defaults(func=_cmd_energy_interp)
    ep_s = ep.add_parser("set", help="Compute + sync-caput every motor")
    ep_s.add_argument("keV", type=float)
    ep_s.add_argument("--dry-run", action="store_true",
                      help="Report targets, do not caput")
    ep_s.add_argument("--no-regime", action="store_true")
    ep_s.set_defaults(func=_cmd_energy_set)

    # qgmax
    qp = sub.add_parser("qgmax", help="pystream QGMax trigger + status")\
        .add_subparsers(dest="cmd", required=True)
    qp_t = qp.add_parser("trigger", help="Write the QGMax request file")
    qp_t.set_defaults(func=_cmd_qgmax_trigger)
    qp_s = qp.add_parser("status", help="Read the QGMax response file")
    qp_s.set_defaults(func=_cmd_qgmax_status)

    # autofocus
    af = sub.add_parser("autofocus", help="Scintillator-screen autofocus sweep")
    af.add_argument("--motor", required=True, help="Focus motor PV")
    af.add_argument("--image-pv", default="32idbSP1:Pva1:Image")
    af.add_argument("--half-range", type=float, default=1.0)
    af.add_argument("--steps", type=int, default=21)
    af.add_argument("--exposure", type=float, default=None)
    af.add_argument("--exposure-pv", default="32idbSP1:cam1:AcquireTime")
    af.add_argument("--acquire-pv",  default="32idbSP1:cam1:Acquire")
    af.add_argument("--settle", type=float, default=0.05)
    af.add_argument("--timeout", type=float, default=30.0)
    af.set_defaults(func=_cmd_autofocus)

    # regime
    rp = sub.add_parser("regime", help="Nano/Micro regime state")\
        .add_subparsers(dest="cmd", required=True)
    rp_g = rp.add_parser("get", help="Print the current regime")
    rp_g.set_defaults(func=_cmd_regime_get)
    rp_s = rp.add_parser("set", help="Write nano | micro")
    rp_s.add_argument("mode", choices=("nano", "micro"))
    rp_s.set_defaults(func=_cmd_regime_set)

    return ap


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
