# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.19 — the BSCANE2 outputs are bounded, from the wrapper's own scope.

BSCANE2's CAPTURE/SHIFT/UPDATE/SEL/TDI outputs are unclocked startpoints, so
Vivado timed none of the paths from them into the TAP logic (Slack inf, Path
Group (none); REA-P2.19 baseline, zybo_rea_demo, Vivado 2024.1). The
primitive lives in rr_rea_xilinx7, above rr_rea_top, so a constraint scoped
to hdl.top_module cannot name it (a bound -through rr_rea_top's ports parsed
and changed nothing). constraints/rr_rea_xilinx7_scoped.xdc is declared as a
``{path, ref: rr_rea_xilinx7}`` entry (RTL-P2.1532) instead.

Structural guard (no Vivado): the manifest entry and its ref, that the ref
is the entity holding u_bscane2, that every pin the XDC names is a BSCANE2
port the wrapper maps, and that the bound is half the TCK period
rr_rea_scoped.xdc declares.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
XDC_REL = "constraints/rr_rea_xilinx7_scoped.xdc"
REF = "rr_rea_xilinx7"
WRAPPER = REPO / "rtl" / "rr_rea_jtag_xilinx7.vhd"


def _commands(rel: str) -> list[str]:
    text = re.sub(r"\\\n", " ", (REPO / rel).read_text(encoding="utf-8"))
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _wrapper() -> str:
    return re.sub(r"--[^\n]*", "", WRAPPER.read_text(encoding="utf-8"))


def test_manifest_declares_the_file_scoped_to_the_wrapper():
    manifest = yaml.safe_load((REPO / "ip.yml").read_text(encoding="utf-8"))
    entries = manifest["build"]["sources"]["xdc"]
    assert {"path": XDC_REL, "ref": REF} in entries, entries
    assert (REPO / XDC_REL).is_file()


def test_ref_is_the_entity_that_instantiates_bscane2():
    vhd = _wrapper()
    assert re.search(rf"\bentity\s+{REF}\s+is\b", vhd, re.I)
    assert re.search(r"\bu_bscane2\s*:\s*BSCANE2\b", vhd, re.I)


def test_every_named_pin_is_a_mapped_bscane2_output():
    cmds = _commands(XDC_REL)
    assert len(cmds) == 1 and cmds[0].startswith("set_max_delay -datapath_only")
    pins = re.findall(r"u_bscane2/(\w+)", cmds[0])
    assert sorted(pins) == ["CAPTURE", "SEL", "SHIFT", "TDI", "UPDATE"], pins
    port_map = re.search(r"u_bscane2\s*:\s*BSCANE2.*?port\s+map\s*\((.*?)\);",
                         _wrapper(), re.I | re.S).group(1)
    mapped = {m.group(1).upper() for m in
              re.finditer(r"(\w+)\s*=>\s*(\w+)", port_map)
              if m.group(2).lower() != "open"}
    assert set(pins) <= mapped, f"unmapped: {set(pins) - mapped}"


def test_bound_is_half_the_declared_tck_period():
    tck = [c for c in _commands("constraints/rr_rea_scoped.xdc")
           if c.startswith("create_clock") and "tck_i" in c]
    period = float(re.search(r"-period\s+([\d.]+)", tck[0]).group(1))
    bound = float(re.search(r"-datapath_only\s+([\d.]+)",
                            _commands(XDC_REL)[0]).group(1))
    assert abs(bound - period / 2) < 0.001, (bound, period)
