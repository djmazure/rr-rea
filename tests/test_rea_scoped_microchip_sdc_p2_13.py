# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.13 — the Libero SmartTime constraints reach every REA synchronizer.

SmartTime reads constraints/rr_rea_scoped_microchip.sdc as plain SDC and
matches cells by hierarchical NAME, not by entity, so the synchronizer
selector must match each first stage under every name the netlist gives it.
A synchronizer inside a generate block is named `<generate>.<instance>`:
measured 2026-09-26 (rr_rea_jtag_microchip, MPFS095T, Libero 2025.2,
G_SAMPLE_W 80, G_QUAL_CONDS 1), the first-cut selector `*/u_top/u_cdc_*/s1*`
missed `u_top/g_qual_cdc.u_cdc_qual_mode/s1[0]`, so that crossing was timed
as an ordinary udrck -> sample_clk path (hold -1.262 ns) instead of being
bounded and hold-waived like the others.

Structural guard (no Libero): every rr_rea_sync_word / rr_rea_pulse_xfer
instance in rr_rea_top, including those under a generate, is rendered as the
Libero netlist names it and must match the SDC's target selector.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SDC = REPO / "constraints" / "rr_rea_scoped_microchip.sdc"
SYNC_ENTITIES = ("rr_rea_sync_word", "rr_rea_pulse_xfer")
# Where a RouteRTL consumer's instance sits: <top instance>/u_impl/u_top/...
PREFIX = "u_dbg_rea/u_impl/u_top/"


def _sdc_commands() -> list[str]:
    return [ln.strip() for ln in SDC.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _sync_targets() -> set[str]:
    """The -to get_cells patterns of every bound/waiver in the SDC."""
    targets = set()
    for cmd in _sdc_commands():
        if cmd.startswith(("set_max_delay", "set_min_delay")):
            m = re.search(r"-to\s+\[get_cells\s+\{([^}]*)\}\]", cmd)
            assert m, f"no -to get_cells target in: {cmd}"
            targets.add(m.group(1))
    return targets


def _synchronizer_cells() -> list[str]:
    """Libero-style names of every synchronizer first stage in rr_rea_top."""
    text = re.sub(r"--[^\n]*", "", (REPO / "rtl" / "rr_rea_top.vhd")
                  .read_text(encoding="utf-8"))
    stack: list[str] = []
    cells = []
    for line in text.splitlines():
        gen = re.match(r"\s*(\w+)\s*:\s*(?:if|for)\b.*\bgenerate\b", line, re.I)
        if gen:
            stack.append(gen.group(1))
            continue
        if re.match(r"\s*end\s+generate\b", line, re.I):
            stack.pop()
            continue
        inst = re.match(r"\s*(\w+)\s*:\s*(?:entity\s+\w+\.)?(\w+)\b", line)
        if inst and inst.group(2) in SYNC_ENTITIES:
            name = ".".join(stack + [inst.group(1)])
            cells.append(f"{PREFIX}{name}/s1[0]")
    return cells


def test_selector_matches_every_synchronizer_including_generate_nested():
    cells = _synchronizer_cells()
    assert len(cells) >= 20, f"found only {len(cells)} synchronizers — parser drift?"
    nested = [c for c in cells if "." in c.split("/")[-2]]
    assert nested, "no generate-nested synchronizer found — parser drift?"
    targets = _sync_targets()
    assert len(targets) == 1, f"bound and hold waiver must share one selector: {targets}"
    (target,) = targets
    missed = [c for c in cells if not fnmatch.fnmatchcase(c, target)]
    assert not missed, (
        f"{target} misses {missed}: those crossings are timed as ordinary "
        f"inter-clock paths, neither bounded nor hold-waived")


def test_bound_and_hold_waiver_both_present_and_no_clock_groups():
    cmds = _sdc_commands()
    assert any(c.startswith("create_clock") and "u_ujtag/UDRCK" in c for c in cmds)
    assert any(c.startswith("set_max_delay") for c in cmds)
    assert any(c.startswith("set_min_delay") for c in cmds)
    assert not any("set_clock_groups" in c for c in cmds), (
        "set_clock_groups waives the crossing instead of bounding it")


def test_manifest_ships_it_to_libero_only_through_rr_own_reader():
    """ip.yml declares the file under sdc_per_vendor.microchip (applied on
    Libero builds only, RTL-P2.1392), read with rr's OWN manifest helper when
    it is importable, and keeps the Quartus file as the bare `sdc`."""
    import sys
    import yaml
    manifest = yaml.safe_load((REPO / "ip.yml").read_text(encoding="utf-8"))
    rel = "constraints/rr_rea_scoped_microchip.sdc"
    try:
        sys.path.insert(0, str(REPO.parent / "routertl"))
        from routertl_core.ip_yml import normalize_ip_yml, sdc_list, sdc_per_vendor
        norm = normalize_ip_yml(manifest)
        per_vendor, bare = sdc_per_vendor(norm), sdc_list(norm)
    except ImportError:
        srcs = manifest["build"]["sources"]
        per_vendor, bare = srcs.get("sdc_per_vendor", {}), srcs.get("sdc", [])
    assert per_vendor.get("microchip") == [rel], f"sdc_per_vendor = {per_vendor}"
    assert rel not in bare, "a bare sdc entry would reach Quartus, which cannot parse it"
    assert (REPO / rel).is_file()
