# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.20 (REA-REQ-969): rr_rea_intel with G_DR_GUARD = true, scanned
through the sld_virtual_jtag mock with 50-bit guarded frames. VERSION (odd)
reads exact, FEATURES[31] advertises the guard, and an odd value written to
PRETRIG reads back exact. Proves the generic reaches the core through the
wrapper the Arria 10 build instantiates."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402
from cocotb.triggers import ClockCycles  # noqa: E402

import rea_wrapper_generics as rwg  # noqa: E402
from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

DR_W = 50
ADDR_VERSION, ADDR_PRETRIG = 0x00, 0x14
VERSION = 0x5245410F


def _gframe(addr: int, data: int, write: bool) -> int:
    return (int(write) << 49) | ((addr & 0xFFFF) << 33) | ((data & 0xFFFF_FFFF) << 1)


async def _gread(dut, addr: int) -> tuple[int, int]:
    """Return (first bit out, 32-bit value) of a guarded read."""
    await rwg._phase(dut, capture=1)
    await rwg._shift_dr(dut, _gframe(addr, 0, False), DR_W)
    await rwg._phase(dut, update=1)
    await ClockCycles(dut.tck_i, 2)
    await rwg._phase(dut, capture=1)
    out = await rwg._shift_dr(dut, _gframe(addr, 0, False), DR_W)
    dut.sel_i.value = 0
    return out & 1, (out >> 1) & 0xFFFF_FFFF


async def _gwrite(dut, addr: int, data: int):
    await rwg._phase(dut, capture=1)
    await rwg._shift_dr(dut, _gframe(addr, data, True), DR_W)
    await rwg._phase(dut, update=1)
    dut.sel_i.value = 0


@cocotb.test()
@requires("REA-REQ-969")
async def test_guarded_intel_reads_odd_values_exact(dut):
    await rwg.start(dut)
    guard, version = await _gread(dut, ADDR_VERSION)
    assert (guard, version) == (0, VERSION), (
        f"REA-REQ-969: guarded VERSION read gave guard={guard} "
        f"value=0x{version:08X}, expected guard=0 value=0x{VERSION:08X}")
    _, features = await _gread(dut, rwg.ADDR_FEATURES)
    assert (features >> 31) & 1 == 1, (
        f"REA-REQ-969: FEATURES=0x{features:08X} [31]=0 — the wrapper did not "
        "pass G_DR_GUARD to rr_rea_top")
    rwg.check_features(features & ~(1 << 31))
    for value in (0x2B, 0x11, 0x2A):
        await _gwrite(dut, ADDR_PRETRIG, value)
        await ClockCycles(dut.tck_i, 4)
        guard, got = await _gread(dut, ADDR_PRETRIG)
        assert (guard, got) == (0, value), (
            f"REA-REQ-969: PRETRIG wrote 0x{value:02X}, read guard={guard} "
            f"value=0x{got:08X}")


def main() -> None:
    run_simulation(
        top_level="rr_rea_wrapper_harness",
        module="test_rea_dr_guard_intel_p2_20",
        custom_libraries=rwg.libraries(),
        generics={**rwg.GENERICS, "G_VENDOR": "intel", "G_DR_GUARD": True},
        waves=True,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
