# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_top — BUILD_ID injection path (RTL-P3.1203).
#
# Proves the full injection chain: a non-zero C_REA_BUILD_ID in
# rr_rea_build_id_pkg is read DIRECTLY by rr_rea_regbank (use work.rr_rea_build_id_pkg
# -> when C_ADDR_BUILD_ID => rd_data <= C_REA_BUILD_ID) and reads back at BUILD_ID
# (0xD4) over JTAG. Here the standalone fixture (=0) is REPLACED by a fixture package
# carrying x"DEADBEEF" — exactly what the build-flow regeneration does with the real
# source hash. This is the sim proof that the build flow's regenerated package reaches
# the on-chip register with no instantiation edits (no generic, no top-level plumbing).
#
# Run via: rr sim run test_rea_build_id_p3_1203
#     or:  python test_rea_build_id_p3_1203.py  (with PYTHONPATH set)

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": 256,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIXTURES = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_VERSION  = 0x00
ADDR_BUILD_ID = 0xD4

EXPECTED_VERSION  = 0x52454107
EXPECTED_BUILD_ID = 0xDEADBEEF  # from the fixture package (not the stub)


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_build_id_p3_1203",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                # Fixture INSTEAD of rtl/rr_rea_build_id_pkg.vhd (the stub):
                # carries a known non-zero C_REA_BUILD_ID.
                f"{_FIXTURES}/rr_rea_build_id_deadbeef.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── JTAG protocol helpers (same wire format as test_rea_top.py) ──────

TCK_PERIOD_NS    = 25.0
SAMPLE_PERIOD_NS = 8.0


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst.value = 1
    dut.arst.value = 1
    dut.tdi.value = 0
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 0
    dut.sel.value = 0
    dut.probe_in.value = 0
    await ClockCycles(dut.tck, 4)
    dut.sample_rst.value = 0
    dut.arst.value = 0
    await ClockCycles(dut.tck, 1)


async def _capture_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 1
    dut.shift_en.value = 0
    dut.update.value = 0
    await RisingEdge(dut.tck)
    dut.capture.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    from cocotb.triggers import NextTimeStep
    dut.sel.value = 1
    dut.capture.value = 0
    dut.update.value = 0
    dut.shift_en.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo.value) & 1) << i
        await RisingEdge(dut.tck)
    dut.shift_en.value = 0
    return out


async def _update_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 1
    await RisingEdge(dut.tck)
    dut.update.value = 0
    dut.sel.value = 0


def _frame(addr: int, data: int, write: bool) -> int:
    return ((1 if write else 0) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)


async def _jtag_read(dut, addr: int) -> int:
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)
    await ClockCycles(dut.tck, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


@cocotb.test()
@requires("REA-REQ-015")
async def test_rea_req_015_build_id_injection(dut):
    """A regenerated build-id package (fixture = 0xDEADBEEF) reaches BUILD_ID
    (0xD4) via a direct C_REA_BUILD_ID read in the regbank, no instantiation edit.
    Hard-coded per ROUTERTL-002."""
    await _start_clocks(dut)
    await _reset(dut)

    # Sanity: the TAP works — VERSION reads its magic.
    ver = await _jtag_read(dut, ADDR_VERSION)
    assert ver == EXPECTED_VERSION, (
        f"VERSION over JTAG = 0x{ver:08X}, expected 0x{EXPECTED_VERSION:08X}"
    )

    # The injected build id flows package -> regbank direct read -> BUILD_ID reg.
    bid = await _jtag_read(dut, ADDR_BUILD_ID)
    assert bid == EXPECTED_BUILD_ID, (
        f"BUILD_ID over JTAG = 0x{bid:08X}, expected 0x{EXPECTED_BUILD_ID:08X} "
        f"(the fixture C_REA_BUILD_ID did not reach the register)"
    )

    dut._log.info("RTL-P3.1203 PASS — injected build id reaches BUILD_ID (0xD4)")


if __name__ == "__main__":
    main()
