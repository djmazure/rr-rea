# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P1.1 — pin the FEATURES (0xD0) bit map.

1.11.0 gave FEATURES[23] to the guarded DR while the routertl host had already
read [23] as sample-domain liveness (RTL-P2.1377). This pins every allocation
in rtl/rr_rea_pkg.vhd, so moving or adding a bit is a deliberate edit here,
reviewed against the host's map (routertl sdk/cli/rea/client.py FEAT_*, which
routertl's own parity guard compares with this package).
"""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "rtl" / "rr_rea_pkg.vhd"
_RE = re.compile(r"\bC_FEAT_(\w+?)_(?:BIT|LSB)\s*:\s*natural\s*:=\s*(\d+)\s*;")

EXPECTED = {
    "TRIG_CONDS": 0, "NUM_SOURCE": 8, "WIDE_SAMPLE": 16, "WIDE_COND": 17,
    "TIMESTAMP": 18, "READBACK_INTEGRITY": 19, "AXIS_WINDOW": 20,
    "UDP_WINDOW": 21, "STORAGE_QUAL": 22, "SAMPLE_LIVENESS": 23,
    "QUAL_CONDS": 24, "TRIG_STAGES": 28, "DR_GUARD": 31,
}
WIDTH = {"TRIG_CONDS": 8, "NUM_SOURCE": 8, "QUAL_CONDS": 4, "TRIG_STAGES": 3}


def _fields() -> dict[str, int]:
    return {n: int(v) for n, v in _RE.findall(PKG.read_text(encoding="utf-8"))}


def test_bitmap_is_pinned():
    assert _fields() == EXPECTED


def test_no_two_fields_share_a_bit():
    owner: dict[int, str] = {}
    for name, lsb in _fields().items():
        for bit in range(lsb, lsb + WIDTH.get(name, 1)):
            assert bit not in owner, f"FEATURES[{bit}]: {owner[bit]} and {name}"
            assert bit <= 31
            owner[bit] = name


def test_discriminator_cores_have_an_even_features():
    # FEATURES reads exact on a part with the RTL-P2.901 odd-value fault only
    # if it is even, and bit 0 is G_TRIG_CONDS' LSB. Every core on the
    # discriminator top must therefore keep an even G_TRIG_CONDS, or the
    # host cannot identify it there (rr ila readback-map refuses an all-ones
    # FEATURES).
    assert all(lsb != 0 for n, lsb in _fields().items() if n != "TRIG_CONDS")
    top = (PKG.parents[1] / "examples" / "dr_guard_discriminator"
           / "rr_rea_dr_guard_discriminator_top.vhd").read_text(
        encoding="utf-8")
    assert "G_TRIG_CONDS" not in top, "override must stay even; re-check"
    wrapper = (PKG.parent / "rr_rea_jtag_intel.vhd").read_text(encoding="utf-8")
    default = re.search(r"G_TRIG_CONDS\s*:\s*positive\s*:=\s*(\d+)", wrapper)
    assert default and int(default.group(1)) % 2 == 0, default
