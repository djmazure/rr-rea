# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.22 — rr_rea_xilinx7 exposes optional TAP mirror outputs.

A consumer that needs the raw TAP signals of the REA's BSCANE2 had no port
for them, so it had to patch the vendored wrapper, which a package update
then overwrote. The wrapper now drives four optional outputs: tap_tck,
tap_tms and tap_tdi (BSCANE2's TCK/TMS/TDI) and tap_tdo (this core's TDO
drive). Connecting TMS makes it an unclocked primitive-output startpoint, so
the wrapper-scoped XDC bounds it like CAPTURE/SHIFT/UPDATE/SEL/TDI.

Structural guard (no simulator): the port contract, that BSCANE2 TMS is no
longer left open, and that the XDC names TMS. The cocotb mirror check is
sim/cocotb/tests/test_rea_xilinx7_tap_mirror_p2_22.py.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "rtl" / "rr_rea_jtag_xilinx7.vhd"
XDC = REPO / "constraints" / "rr_rea_xilinx7_scoped.xdc"
MIRRORS = ("tap_tck", "tap_tms", "tap_tdi", "tap_tdo")


def _vhd() -> str:
    return re.sub(r"--[^\n]*", "", WRAPPER.read_text(encoding="utf-8"))


def _entity_ports() -> dict[str, str]:
    ent = re.search(r"\bentity\s+rr_rea_xilinx7\s+is(.*?)\bend\s+entity",
                    _vhd(), re.I | re.S).group(1)
    port_block = re.search(r"\bport\s*\((.*)\)\s*;", ent, re.I | re.S).group(1)
    return {m.group(1).lower(): " ".join(m.group(2).lower().split())
            for m in re.finditer(r"(\w+)\s*:\s*([^;]+)", port_block)}


def test_wrapper_declares_the_four_mirror_outputs():
    ports = _entity_ports()
    for name in MIRRORS:
        assert name in ports, f"rr_rea_xilinx7 has no {name} port: {sorted(ports)}"
        assert ports[name].startswith("out std_logic"), (name, ports[name])
        assert ":=" not in ports[name], f"{name} is an output; no default needed"


def test_existing_ports_are_unchanged():
    # Additive: an instantiation written for 1.11.x must still elaborate.
    ports = _entity_ports()
    for name, mode in (("sample_clk_i", "in"), ("sample_rst_i", "in"),
                       ("probe_i", "in"), ("ext_trigger_i", "in"),
                       ("source_o", "out"), ("trigger_o", "out")):
        assert ports[name].startswith(mode + " "), (name, ports[name])


def test_bscane2_tms_is_connected_and_mirrors_are_driven():
    vhd = _vhd()
    port_map = re.search(r"u_bscane2\s*:\s*BSCANE2.*?port\s+map\s*\((.*?)\);",
                         vhd, re.I | re.S).group(1)
    tms = re.search(r"\bTMS\s*=>\s*(\w+)", port_map, re.I).group(1)
    assert tms.lower() != "open", "BSCANE2 TMS is still left open"
    drives = {m.group(1).lower(): m.group(2).lower()
              for m in re.finditer(r"^\s*(tap_\w+)\s*<=\s*(\w+)\s*;", vhd, re.I | re.M)}
    assert drives.get("tap_tms") == tms.lower(), drives
    assert drives.get("tap_tck") == "tck_i", drives
    assert drives.get("tap_tdi") == "tdi_i", drives
    assert drives.get("tap_tdo") == "tdo_o", drives


def test_xdc_bounds_tms_with_the_other_bscane2_outputs():
    text = re.sub(r"\\\n", " ", XDC.read_text(encoding="utf-8"))
    cmds = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    assert len(cmds) == 1 and cmds[0].startswith("set_max_delay -datapath_only")
    pins = sorted(re.findall(r"u_bscane2/(\w+)", cmds[0]))
    assert pins == ["CAPTURE", "SEL", "SHIFT", "TDI", "TMS", "UPDATE"], pins
