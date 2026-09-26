# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.12 — the Quartus twin of the scoped XDC ships, and stays honest.

Quartus does not constrain the sld_virtual_jtag clock on its own. Measured
2026-09-26 on an rr_rea_intel build (DE25-Standard, A5ED013BB32AE4SCS,
Quartus Pro 25.3.1): report_ucp "Unconstrained Clocks: 1", clock status
"altera_reserved_tck ... Unconstrained" (warning 332060). The routertl DE25
and Arria 10 REA demos never applied their own set_clock_groups either (only
the board master.sdc reached the qsf), so every Altera REA build so far ran
its JTAG domain untimed. constraints/rr_rea_scoped.sdc fixes that: applied by
quartus_sta to that build it gave 0 unconstrained clocks and a 10.000 ns net
bound (0.5 * min(33.333, 20)) on all 327 synchronizer first stages.

rr applies a package SDC GLOBALLY (ip.lock scoped_constraints kind: sdc,
RTL-P3.1331), so the file must scope itself. This guard is structural (no
Quartus): it pins the manifest wiring, the file's three properties, and the
entity-name contract its selectors rely on.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SDC_REL = "constraints/rr_rea_scoped.sdc"
S1_SELECTOR = "{*|rr_rea_sync_word:*|s1* *|rr_rea_pulse_xfer:*|s1*}"


def _sdc_text() -> str:
    """SDC with comments removed and continuations joined."""
    text = (REPO / SDC_REL).read_text(encoding="utf-8")
    text = re.sub(r"\\\n", " ", text)
    return "\n".join(ln for ln in text.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))


def _strip_vhdl_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def test_manifest_ships_the_scoped_sdc_through_rr_own_reader():
    """ip.yml build.sources.sdc declares the file, read with rr's OWN
    manifest helper (the one its install path uses), and the file exists."""
    manifest = yaml.safe_load((REPO / "ip.yml").read_text(encoding="utf-8"))
    try:
        sys.path.insert(0, str(REPO.parent / "routertl"))
        from routertl_core.ip_yml import normalize_ip_yml, sdc_list
        declared = sdc_list(normalize_ip_yml(manifest))
    except ImportError:
        declared = (manifest.get("build", {}).get("sources", {}) or {}).get("sdc", [])
    assert SDC_REL in declared, f"ip.yml build.sources.sdc = {declared}"
    assert (REPO / SDC_REL).is_file()


def test_sdc_declares_the_jtag_clock_without_overriding_a_consumer_one():
    sdc = _sdc_text()
    assert re.search(
        r"create_clock\s+-name\s+\{altera_reserved_tck\}\s+-period\s+33\.333",
        sdc), "no create_clock on altera_reserved_tck — the JTAG domain is untimed"
    # The create_clock sits behind the "already clocked?" check, so a
    # board-level JTAG constraint is never silently replaced.
    guard = sdc.index("if {!$_rr_rea_tck_clocked}")
    assert guard < sdc.index("create_clock"), (
        "create_clock must be guarded by the already-clocked check")


def test_sdc_bounds_every_crossing_and_never_waives_one():
    sdc = _sdc_text()
    assert S1_SELECTOR in sdc, "the synchronizer selector changed"
    fp = re.findall(r"set_false_path\s+(.*)", sdc)
    nd = re.findall(r"set_net_delay\s+(.*)", sdc)
    assert fp and nd, "crossings are neither cut-and-bounded"
    for cut in fp:
        target = re.search(r"-to\s+(\S+)", cut).group(1)
        assert any(re.search(r"-to\s+" + re.escape(target) + r"\b", b)
                   for b in nd), (
            f"set_false_path -to {target} has no set_net_delay on the same "
            f"target: a bare cut waives the crossing (owner CDC rule)")
    assert re.search(
        r"set_net_delay\s.*-max\s+-get_value_from_clock_period\s+min_clock_period"
        r"\s+-value_multiplier\s+0\.5\b", sdc), (
        "the bound must be half the FASTER period, derived from the live "
        "clocks — the Xilinx twin's 0.5 * min(T_tck, T_sample)")
    assert "set_clock_groups" not in sdc, (
        "set_clock_groups waives the crossing instead of bounding it "
        "(SPEC 'Clock-domain crossings')")


def test_selector_entities_exist_and_first_stage_is_s1():
    """The SDC matches by ENTITY name, so every synchronizer in rr_rea_top
    must be one of the two entities, each with first stage `s1`."""
    top = _strip_vhdl_comments(
        (REPO / "rtl" / "rr_rea_top.vhd").read_text(encoding="utf-8"))
    kinds = re.findall(
        r"^\s*\w+\s*:\s*(?:entity\s+\w+\.)?(rr_rea_\w+)\b", top, re.M | re.I)
    syncs = [k for k in kinds if k in ("rr_rea_sync_word", "rr_rea_pulse_xfer")]
    assert len(syncs) >= 20, f"found only {len(syncs)} synchronizers — parser drift?"
    cdc = _strip_vhdl_comments(
        (REPO / "rtl" / "rr_rea_cdc.vhd").read_text(encoding="utf-8"))
    declared = set(re.findall(r"^\s*entity\s+(\w+)\s+is", cdc, re.M | re.I))
    for entity in ("rr_rea_sync_word", "rr_rea_pulse_xfer"):
        assert entity in declared, f"{entity} renamed — the SDC matches nothing"
        arch = cdc[cdc.index(f"architecture rtl of {entity}"):]
        arch = arch[:arch.index("end architecture")]
        assert re.search(r"\bsignal\s+s1\b", arch), (
            f"{entity}'s first stage is no longer `s1`")
    # Any OTHER rr_rea_cdc entity used in rr_rea_top as a data synchronizer
    # would escape the selector; the reset synchronizer is the only one.
    others = {k for k in kinds if k.startswith("rr_rea_") and k in declared}
    assert others <= {"rr_rea_sync_word", "rr_rea_pulse_xfer", "rr_rea_rst_sync"}, (
        f"new rr_rea_cdc entity {sorted(others)} in rr_rea_top is not covered "
        f"by rr_rea_scoped.sdc")
