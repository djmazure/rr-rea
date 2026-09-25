# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.8 — REA ships its own timing constraints, and they stay honest.

Vivado does not derive a clock on a user-instantiated BSCANE2 TCK. Measured
2026-09-25 (rr_rea_xilinx7 OOC synth, Vivado 2024.1): 993 register pins with
"no clock", TCK listed under Unconstrained Clocks. So without a shipped
constraint the whole JTAG domain of every Xilinx REA build is untimed. The
fix is constraints/rr_rea_scoped.xdc, declared in ip.yml build.sources.xdc so
rr applies it to consumers as `read_xdc -ref rr_rea_top` (RTL-P2.636). Applied
to that probe it gave 0 unclocked pins and a 5.000 ns datapath-only bound on
the crossings (0.5 * min(33.333, 10)).

This guard is structural (no simulator or Vivado). It pins what could rot
silently: the manifest <-> file wiring, the three properties of the XDC, and
the naming contract its selector relies on (every synchronizer in
rr_rea_top is a `u_cdc_*` instance whose first stage is `s1`). A new
synchronizer with another label would be silently unbounded.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
XDC_REL = "constraints/rr_rea_scoped.xdc"


def _xdc_commands() -> list[str]:
    """XDC statements with comments removed and continuations joined."""
    text = (REPO / XDC_REL).read_text(encoding="utf-8")
    text = re.sub(r"\\\n", " ", text)
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _strip_vhdl_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def test_manifest_ships_the_scoped_xdc_through_rr_own_reader():
    """ip.yml build.sources.xdc declares the file, read with rr's OWN manifest
    helper (the one its publish/scan paths use), and the file exists."""
    manifest = yaml.safe_load((REPO / "ip.yml").read_text(encoding="utf-8"))
    try:
        sys.path.insert(0, str(REPO.parent / "routertl"))
        from routertl_core.ip_yml import normalize_ip_yml, xdc_list
        declared = xdc_list(normalize_ip_yml(manifest))
    except ImportError:
        declared = (manifest.get("build", {}).get("sources", {}) or {}).get("xdc", [])
    assert XDC_REL in declared, f"ip.yml build.sources.xdc = {declared}"
    assert (REPO / XDC_REL).is_file()
    assert manifest["hdl"]["top_module"] == "rr_rea_top", (
        "rr scopes the XDC with -ref hdl.top_module; the XDC's get_ports "
        "tck_i / sample_clk_i are rr_rea_top ports")


def test_xdc_declares_tck_bounds_crossings_and_never_waives():
    cmds = _xdc_commands()
    joined = "\n".join(cmds)
    assert re.search(r"^create_clock\s.*\[get_ports tck_i\]", joined, re.M), (
        "no create_clock on tck_i — the JTAG domain would be untimed")
    assert re.search(r"set_max_delay\s+-datapath_only\s.*u_cdc_\*/s1_reg\*",
                     joined), "crossings into the synchronizers are not bounded"
    assert re.search(r"expr\s*\{\s*0\.5\s*\*\s*min\(", joined), (
        "the bound must be half the faster period, derived — not a literal")
    for forbidden in ("set_clock_groups", "set_false_path"):
        assert forbidden not in joined, (
            f"{forbidden} waives the crossing instead of bounding it "
            f"(owner CDC rule; SPEC 'Clock-domain crossings')")
    assert not any(c.split()[0] == "if" for c in cmds), (
        "Vivado's XDC parser rejects 'if' (Designutils 20-1307) and silently "
        "skips the block (RTL-P2.1381)")


def test_every_rr_rea_top_synchronizer_matches_the_xdc_selector():
    top = _strip_vhdl_comments(
        (REPO / "rtl" / "rr_rea_top.vhd").read_text(encoding="utf-8"))
    insts = re.findall(
        r"^\s*(\w+)\s*:\s*(?:entity\s+\w+\.)?(rr_rea_sync_word|rr_rea_pulse_xfer)\b",
        top, re.M | re.I)
    assert len(insts) >= 20, f"found only {len(insts)} synchronizers — parser drift?"
    stray = [label for label, _kind in insts if not label.startswith("u_cdc_")]
    assert not stray, (
        f"synchronizer instance(s) {stray} are not named u_cdc_* — "
        f"rr_rea_scoped.xdc's set_max_delay would not reach them")
    cdc = _strip_vhdl_comments(
        (REPO / "rtl" / "rr_rea_cdc.vhd").read_text(encoding="utf-8"))
    for entity in ("rr_rea_sync_word", "rr_rea_pulse_xfer"):
        arch = cdc[cdc.index(f"architecture rtl of {entity}"):]
        arch = arch[:arch.index("end architecture")]
        assert re.search(r"\bsignal\s+s1\b", arch), (
            f"{entity}'s first stage is no longer `s1` — the XDC selector "
            f"*/s1_reg* would match nothing")
        assert re.search(r'ASYNC_REG\s+of\s+s1\s*:\s*signal\s+is\s+"TRUE"', arch)
