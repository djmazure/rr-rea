# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.13 — the scoped XDC parses cleanly before the sample clock exists.

Vivado parses constraints/rr_rea_scoped.xdc at consumer synthesis before the
consumer's own create_clock has run. Measured 2026-10-04 (zybo_rea_demo,
rr-rea 1.11.1, synth runme.log lines 300-304): `get_clocks -of_objects
[get_ports sample_clk_i]` found nothing (Vivado 12-1008), the period lookup
never set rr_rea_t_sample, and the next line raised CRITICAL WARNING Common
17-1548. Every Xilinx REA consumer carried that critical warning.

This test runs the file's own derivation lines in a real Tcl interpreter
(tclsh), with the Vivado object commands stubbed to model the two parse
passes. The stubs follow what Vivado does: with no clocks, get_clocks returns
an empty list and get_property on an empty list fails unless -quiet is given.

  synthesis pass       no sample clock: must evaluate without error
  implementation pass  sample clock present: bound = 0.5 * min(TCK, sample)

The consumer synthesis log is the real proof (0 critical warnings from this
file), and so is the routed clock_interaction (both crossings still bounded).
This test keeps the guard from being dropped without anyone noticing.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
XDC = REPO / "constraints" / "rr_rea_scoped.xdc"
TCLSH = shutil.which("tclsh") or shutil.which("tclsh8.6")

# Vivado's object commands, modelled for the parse. $::sample_period is the
# consumer's clock period, or "" before the consumer has created it.
_STUBS = r"""
proc get_ports {args} { return [lindex $args end] }
proc get_clocks {args} {
    if {$::sample_period eq ""} { return {} }
    return clk_sample
}
proc get_property {args} {
    set quiet [expr {[lsearch -exact $args -quiet] >= 0}]
    set objs [lindex $args end]
    if {[llength $objs] == 0} {
        if {$quiet} { return {} }
        error "get_property: no objects"
    }
    return $::sample_period
}
"""


def _derivation() -> str:
    """The file's `set rr_rea_*` statements, in order."""
    text = re.sub(r"\\\n", " ", XDC.read_text(encoding="utf-8"))
    lines = [ln.strip() for ln in text.splitlines()
             if re.match(r"\s*set rr_rea_\w+\s", ln)]
    assert lines, f"no `set rr_rea_*` derivation in {XDC.name}"
    return "\n".join(lines)


def _bound(sample_period: str) -> tuple[int, str]:
    script = (f"set ::sample_period {{{sample_period}}}\n{_STUBS}\n"
              f"{_derivation()}\nputs $rr_rea_bound\n")
    proc = subprocess.run([TCLSH], input=script, capture_output=True,
                          text=True, errors="replace", timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


pytestmark = pytest.mark.skipif(TCLSH is None, reason="tclsh not installed")


def test_synthesis_pass_without_the_sample_clock_parses_cleanly():
    rc, out = _bound("")
    assert rc == 0, (
        "the derivation fails when the consumer's sample clock does not exist "
        f"yet; Vivado reports it as CRITICAL WARNING Common 17-1548:\n{out}")
    assert float(out) == pytest.approx(0.5 * 33.333)


@pytest.mark.parametrize("period, bound", [
    ("8.000", 4.0),        # zybo_rea_demo sys_clk_pin, measured routed bound 4.000 ns
    ("10.000", 5.0),       # REA-P2.8 OOC probe bound 5.000 ns
    ("50.000", 0.5 * 33.333),  # a sample clock slower than TCK: TCK sets the bound
])
def test_implementation_pass_derives_the_bound_from_the_live_clock(period, bound):
    rc, out = _bound(period)
    assert rc == 0, out
    assert float(out) == pytest.approx(bound)
