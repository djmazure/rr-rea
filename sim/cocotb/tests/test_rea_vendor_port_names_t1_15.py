# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea — vendor primitives must keep their OWN port names (RTL-T1.15).
#
# This is a PYTEST (not a cocotb test): it inspects source text, and the thing
# it guards is invisible to simulation by construction — the cocotb suite MOCKS
# the JTAG wrappers, so a broken vendor component declaration sims green.
#
# Background. The ESA _i/_o rename (b7d270c) was applied with a blunt token
# rewriter that did not stop at the vendor boundary. It rewrote the COMPONENT
# DECLARATION of sld_virtual_jtag — an Intel megafunction — from tck/tdi/tdo to
# tck_i/tdi_i/tdo_o. Quartus binds component ports BY NAME to the real
# primitive, so elaboration died with
#     Error (13870): design entity "sld_virtual_jtag" does not contain
#     port "tck_i" specified in associated component
# and rr-rea HEAD could not synthesise for Altera AT ALL for ten days. It
# survived because 105 cocotb tests and 5 PROVED formal harnesses all pass
# without ever elaborating that wrapper against a real vendor library.
#
# The ESA convention governs OUR entities. A vendor primitive's ports are
# fixed by the vendor.

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RTL = Path(__file__).resolve().parents[3] / "rtl"

#: Vendor primitives instantiated anywhere in this IP, with their REAL port
#: names. Extend this table when a new vendor primitive is introduced — that is
#: the deliberate cost of reaching outside the repo for a hard macro.
_VENDOR_PRIMITIVES: dict[str, set[str]] = {
    # Intel/Altera megafunction (rr_rea_jtag_intel.vhd).
    "sld_virtual_jtag": {
        "tck", "tdi", "tdo",
        "virtual_state_cdr", "virtual_state_sdr", "virtual_state_udr",
        "ir_in", "ir_out",
    },
}

#: Suffixes the ESA convention adds to OUR ports. None may appear on a vendor
#: primitive's ports.
_ESA_SUFFIXES = ("_i", "_o")


def _component_blocks(text: str) -> dict[str, str]:
    """Map component-name -> its declaration body."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r"^\s*component\s+(\w+)\s+is\b(.*?)^\s*end\s+component\s*;",
        text, re.S | re.M | re.I,
    ):
        out[m.group(1).lower()] = m.group(2)
    return out


def _declared_ports(body: str) -> set[str]:
    """Port names inside a component declaration's `port (...)` clause."""
    m = re.search(r"\bport\s*\((.*)\)\s*;\s*$", body, re.S | re.I)
    region = m.group(1) if m else body
    ports: set[str] = set()
    for line in region.splitlines():
        code = line.split("--", 1)[0]
        pm = re.match(r"\s*(\w+)\s*:\s*(in|out|inout|buffer)\b", code, re.I)
        if pm:
            ports.add(pm.group(1).lower())
    return ports


def _vhd_sources() -> list[Path]:
    return sorted(_RTL.glob("*.vhd"))


@pytest.mark.parametrize("primitive", sorted(_VENDOR_PRIMITIVES))
def test_vendor_component_declaration_matches_the_real_primitive(primitive):
    """A vendor component decl must name the vendor's ports, exactly."""
    expected = _VENDOR_PRIMITIVES[primitive]
    seen_anywhere = False
    for path in _vhd_sources():
        blocks = _component_blocks(path.read_text(encoding="utf-8",
                                                 errors="replace"))
        body = blocks.get(primitive)
        if body is None:
            continue
        seen_anywhere = True
        got = _declared_ports(body)
        assert got == expected, (
            f"{path.name}: component {primitive} declares ports {sorted(got)}, "
            f"but the vendor primitive has {sorted(expected)}.\n"
            f"  unexpected: {sorted(got - expected)}\n"
            f"  missing:    {sorted(expected - got)}\n"
            "  Quartus/Vivado bind component ports BY NAME — a renamed vendor "
            "port fails elaboration (Error 13870) even though sim stays green, "
            "because the wrappers are mocked. The ESA _i/_o convention stops "
            "at the vendor boundary."
        )
    assert seen_anywhere, (
        f"{primitive} is in the guard table but no rtl/*.vhd declares it. "
        "Either the primitive was dropped (remove the table entry) or the "
        "declaration was reshaped so this guard no longer sees it — which "
        "would make the guard silently vacuous."
    )


def test_no_vendor_primitive_port_carries_an_esa_suffix():
    """Blanket check across every known vendor primitive."""
    offenders: list[str] = []
    for path in _vhd_sources():
        blocks = _component_blocks(path.read_text(encoding="utf-8",
                                                  errors="replace"))
        for primitive in _VENDOR_PRIMITIVES:
            body = blocks.get(primitive)
            if body is None:
                continue
            for port in sorted(_declared_ports(body)):
                if port.endswith(_ESA_SUFFIXES):
                    offenders.append(f"{path.name}:{primitive}.{port}")
    assert not offenders, (
        "vendor primitive ports carrying an ESA _i/_o suffix: "
        f"{offenders}. These are the vendor's names, not ours."
    )
