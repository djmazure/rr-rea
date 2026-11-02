# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P1.4 — rr_rea_scoped.sdc bounds the skew across each multi-bit word.

Quartus Design Assistant rule CDC-50007 (High, "CDC Bus Constructed with
Multi-bit Synchronizer Chains with Insufficient Constraints") listed every
multi-bit rr_rea_sync_word word on the DE25 build (REA 1.12.3, Quartus Pro
25.3.1): 13 rows, Max Skew "Unconstrained", Net Delay "Constrained". An
integration that gates on Design Assistant High rows could not build with REA.

Measured on that build with quartus_sta:
  no set_max_skew                               CDC-50007 13 rows
  0.8 x destination period, per word            CDC-50007  4 rows (the four
      sample clock -> altera_reserved_tck words: 26.666 ns > 10 ns source)
  0.8 x the faster period, per word (shipped)   CDC-50007  0 rows

This test runs the SDC itself in a real Tcl interpreter with the Quartus
commands stubbed, on a synthetic register list, and checks the constraints it
issues: one set_max_skew per multi-bit word, exactly that word's first-stage
bits, the faster period, and nothing for 1-bit words or rr_rea_pulse_xfer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SDC = REPO / "constraints" / "rr_rea_scoped.sdc"
TCLSH = shutil.which("tclsh") or shutil.which("tclsh8.6")

pytestmark = pytest.mark.skipif(TCLSH is None, reason="REA-T3.5: tclsh not installed")

# First-stage registers of a synthetic netlist, as Quartus Pro names them.
SYNC_WORDS = {
    "u_rea|u_top|u_cdc_trig_mode": 3,       # multi-bit word
    "u_rea|u_top|u_cdc_trig_mode2": 2,      # name prefix of the word above
    "u_rea|u_top|g_cond[1].u_cdc_cond": 2,  # generate index in the path
    "u_rea|u_top|u_cdc_one": 1,             # 1-bit word: no skew bound
}
PULSE_S1 = ["u_rea|u_top|u_px_arm|s1"]      # rr_rea_pulse_xfer, 1 bit

# Collections are Tcl lists of node names. get_registers models the two
# selector kinds the SDC uses: the entity-qualified REA selectors, and a plain
# name pattern, matched with `string match` (which, like Quartus, takes \[ as
# a literal bracket).
_STUBS = r"""
set ::log {}
proc rec {args} {
    # one field per argument; a collection argument is joined with \x1e
    set f {}
    foreach a $args { lappend f [join $a "\x1e"] }
    lappend ::log [join $f "\x1f"]
}
proc get_ports {args} { return {altera_reserved_tck} }
proc get_clocks {args} { return {} }
proc get_clock_info {args} { return {} }
proc get_node_info {opt node} { return $node }
proc get_collection_size {c} { return [llength $c] }
proc foreach_in_collection {var coll body} {
    upvar 1 $var v
    foreach v $coll { uplevel 1 $body }
}
proc get_registers {args} {
    set pats [lindex $args end]
    if {$pats eq "*"} { return ALL }
    set out {}
    # split, not foreach over a list: a list parse would drop the \[ escapes
    # that Quartus reads as literal brackets in a single name pattern.
    foreach p [split $pats " "] {
        if {[string match "*rr_rea_sync_word:*" $p]} {
            set out [concat $out $::sync_s1]
        } elseif {[string match "*rr_rea_pulse_xfer:*" $p]} {
            set out [concat $out $::pulse_s1]
        } else {
            foreach r [concat $::sync_s1 $::pulse_s1] {
                if {[string match $p $r]} { lappend out $r }
            }
        }
    }
    return $out
}
proc create_clock {args} { rec create_clock {*}$args }
proc set_false_path {args} { rec set_false_path {*}$args }
proc set_net_delay {args} { rec set_net_delay {*}$args }
proc set_max_skew {args} { rec set_max_skew {*}$args }
proc post_message {args} { rec post_message {*}$args }
"""


def _run_sdc() -> list[list[str]]:
    sync_s1 = [f"{inst}|s1[{i}]" for inst, n in SYNC_WORDS.items() for i in range(n)]
    script = (
        _STUBS
        + "set ::sync_s1 [list " + " ".join("{" + r + "}" for r in sync_s1) + "]\n"
        + "set ::pulse_s1 [list " + " ".join("{" + r + "}" for r in PULSE_S1) + "]\n"
        + f"source {{{SDC}}}\n"
        + "foreach e $::log { puts $e }\n"
    )
    proc = subprocess.run([TCLSH], input=script, capture_output=True, text=True,
                          errors="replace", timeout=60, check=False)
    assert proc.returncode == 0, f"the SDC failed to evaluate:\n{proc.stderr}"
    return [line.split("\x1f") for line in proc.stdout.split("\n") if line]


def _opt(call: list[str], name: str) -> str:
    return call[call.index(name) + 1]


def test_one_skew_bound_per_multibit_word_covering_exactly_its_bits():
    skews = [c for c in _run_sdc() if c[0] == "set_max_skew"]
    got = {}
    for call in skews:
        assert _opt(call, "-from") == "ALL"
        bits = sorted(_opt(call, "-to").split("\x1e"))
        inst = bits[0].rsplit("|s1[", 1)[0]
        got[inst] = bits
    want = {inst: sorted(f"{inst}|s1[{i}]" for i in range(n))
            for inst, n in SYNC_WORDS.items() if n > 1}
    assert got == want, (
        "each multi-bit word needs its own set_max_skew over exactly its own "
        f"first-stage bits; got {json.dumps(got, indent=1)}")
    assert len(skews) == len(want), "a word is bounded twice"


def test_bound_is_derived_from_the_faster_period_with_margin():
    skews = [c for c in _run_sdc() if c[0] == "set_max_skew"]
    assert skews
    for call in skews:
        assert _opt(call, "-get_skew_value_from_clock_period") == "min_clock_period", (
            "a destination-period bound exceeds the 10 ns source period on the "
            "sample -> altera_reserved_tck words and Design Assistant keeps "
            "CDC-50007 on them (measured on DE25, 4 rows)")
        assert 0 < float(_opt(call, "-skew_value_multiplier")) < 1


def test_no_skew_bound_on_single_bit_crossings():
    targets = " ".join(_opt(c, "-to") for c in _run_sdc() if c[0] == "set_max_skew")
    assert "u_cdc_one|" not in targets
    assert "u_px_arm|" not in targets


def test_existing_cut_and_net_bound_still_cover_every_first_stage():
    log = _run_sdc()
    s1_all = {f"{inst}|s1[{i}]" for inst, n in SYNC_WORDS.items() for i in range(n)}
    s1_all |= set(PULSE_S1)
    for cmd in ("set_false_path", "set_net_delay"):
        calls = [c for c in log if c[0] == cmd]
        assert calls, f"{cmd} is gone"
        assert set(_opt(calls[0], "-to").split("\x1e")) == s1_all


def test_the_sdc_reports_how_many_words_it_bounded():
    msgs = [c for c in _run_sdc() if c[0] == "post_message"]
    text = [" ".join(m).replace("\x1e", " ") for m in msgs]
    assert any("max skew bounded on 3 multi-bit REA words" in m for m in text), text
