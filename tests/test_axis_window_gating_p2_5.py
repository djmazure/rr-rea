# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.5 — the AXI-Stream dump engine is generic-gated OFF by default.

The burst engine (rr_rea_axis_window) must be elaborated only when
G_AXIS_WINDOW is true, so a JTAG-only (or any default) bring-up build
instantiates NO burst engine, drives the m_axis_* master inert, and reads
FEATURES[20] = 0 (REA-REQ-913/914). This is the structural backstop for that
gating: it needs no simulator, so it runs in CI while the cocotb job is blocked
(RTL-T1.26) — the byte-exact burst behaviour itself is proven by the cocotb
test_rea_axis_window_dump_p2_5, and the FEATURES[20]=0 path through both doors
by test_rea_dump_path_contract_p2_4::test_rea_req_913.

If someone drops the generate guard (always instantiating the engine), hard-sets
FEATURES[20], flips the default on, or forgets to tie the master inert when the
engine is absent, this goes red.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOP = (REPO / "rtl" / "rr_rea_top.vhd").read_text()
REGBANK = (REPO / "rtl" / "rr_rea_regbank.vhd").read_text()
PKG = (REPO / "rtl" / "rr_rea_pkg.vhd").read_text()


def test_engine_default_off():
    assert re.search(r"G_AXIS_WINDOW\s*:\s*boolean\s*:=\s*false", TOP), (
        "rr_rea_top must default G_AXIS_WINDOW to false so no bring-up build "
        "instantiates the burst engine unasked"
    )


def test_engine_instantiation_is_generate_guarded():
    # The only instantiation of rr_rea_axis_window must sit inside an
    # `if G_AXIS_WINDOW generate`, never unconditionally.
    assert TOP.count("entity work.rr_rea_axis_window") == 1, (
        "expected exactly one rr_rea_axis_window instantiation"
    )
    guard = re.search(
        r"if\s+G_AXIS_WINDOW\s+generate(.*?)end\s+generate", TOP, re.DOTALL)
    assert guard, "no `if G_AXIS_WINDOW generate` block found in rr_rea_top"
    assert "entity work.rr_rea_axis_window" in guard.group(1), (
        "rr_rea_axis_window is instantiated OUTSIDE the G_AXIS_WINDOW generate "
        "guard — a default core would carry the burst engine (REA-REQ-913)"
    )


def test_master_is_inert_when_engine_absent():
    # The `not G_AXIS_WINDOW` branch must tie the stream valid low.
    off = re.search(
        r"if\s+not\s+G_AXIS_WINDOW\s+generate(.*?)end\s+generate", TOP, re.DOTALL)
    assert off, "no `if not G_AXIS_WINDOW generate` (inert) branch in rr_rea_top"
    assert re.search(r"m_axis_tvalid_o\s*<=\s*'0'", off.group(1)), (
        "with no engine the AXIS master must be inert (m_axis_tvalid_o <= '0')"
    )


def test_features20_is_generic_derived_not_hardcoded():
    # FEATURES[20] must come from the G_AXIS_WINDOW generic, never a bare set.
    assert re.search(
        r"if\s+G_AXIS_WINDOW\s+then\s*\n\s*v\(C_FEAT_AXIS_WINDOW_BIT\)\s*:=\s*'1'",
        REGBANK), (
        "FEATURES[20] must be set from the G_AXIS_WINDOW generic in "
        "build_features, never hand-set (REA-REQ-913)"
    )
    assert "C_FEAT_AXIS_WINDOW_BIT : natural := 20" in PKG
    assert "C_FEAT_UDP_WINDOW_BIT  : natural := 21" in PKG


def test_regbank_never_sets_udp_bit():
    # [21] (udp_window) is Icebox and must never be asserted.
    assert "C_FEAT_UDP_WINDOW_BIT" not in REGBANK, (
        "FEATURES[21] (udp_window) is reserved and must not be set anywhere in "
        "the regbank (REA-REQ-913)"
    )
