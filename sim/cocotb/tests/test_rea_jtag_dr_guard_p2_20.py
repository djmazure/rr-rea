# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# REA-P2.20: rr_rea_jtag_iface with G_DR_GUARD = true (REA-REQ-968).
#
# The guarded DR exists to discriminate the Arria 10 SLD readback fault
# (RTL-P2.901), where every captured value with bit0=1 returns an all-ones
# DR. The hypothesis under test on silicon is that the trigger is TDO=1 at
# the CDR->SDR boundary, so this sim pins the property the experiment relies
# on: after CAPTURE the first bit out is 0 for an ODD register value, and the
# data sits one bit higher. Run with G_DR_GUARD = false it goes red on the
# first assertion (the first bit out is the value's bit0 = 1).

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")

DR_W = 50
VERSION = 0x5245410F  # odd by permanent contract (SPEC VERSION row)


def main() -> None:
    run_simulation(
        top_level="rr_rea_jtag_iface",
        module="test_rea_jtag_dr_guard_p2_20",
        custom_libraries={"work": [f"{_RTL_DIR}/rr_rea_jtag_iface.vhd"]},
        generics={"G_DR_GUARD": True},
        waves=True,
        simulator="nvc",
    )


def guarded_frame(addr: int, data: int, write: bool) -> int:
    """rnw[49] | addr[48:33] | data[32:1] | guard[0] = 0."""
    return (int(write) << 49) | ((addr & 0xFFFF) << 33) | ((data & 0xFFFF_FFFF) << 1)


async def _start(dut):
    cocotb.start_soon(Clock(dut.tck_i, 25.0, unit="ns").start())
    dut.arst_i.value = 1
    for sig in (dut.tdi_i, dut.capture_i, dut.shift_en_i, dut.update_i, dut.sel_i):
        sig.value = 0
    dut.reg_rdata_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)


async def _phase(dut, capture=0, update=0):
    dut.sel_i.value = 1
    dut.capture_i.value = capture
    dut.shift_en_i.value = 0
    dut.update_i.value = update
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0
    dut.update_i.value = 0


async def _shift(dut, value: int, n_bits: int = DR_W) -> list[int]:
    """Shift LSB-first; return TDO bit by bit, sampled before each edge."""
    dut.sel_i.value = 1
    dut.shift_en_i.value = 1
    await ReadOnly()
    await NextTimeStep()
    bits = []
    for i in range(n_bits):
        dut.tdi_i.value = (value >> i) & 1
        await ReadOnly()
        bits.append(int(dut.tdo_o.value) & 1)
        await RisingEdge(dut.tck_i)
    dut.shift_en_i.value = 0
    return bits


def _word(bits: list[int]) -> int:
    return sum(b << i for i, b in enumerate(bits))


@cocotb.test()
@requires("REA-REQ-968")
async def test_first_bit_out_is_guard_zero_for_odd_values(dut):
    """After CAPTURE of an odd value the first TDO bit is the guard (0), and
    bits [32:1] carry the value exactly, for VERSION and for small odd/even
    values on both sides of the bit0 boundary."""
    await _start(dut)
    for value in (VERSION, 0x0000_002B, 0x0000_0011, 0xFFFF_FFFF, 0x0000_002A, 0x0):
        dut.reg_rdata_i.value = value
        await ClockCycles(dut.tck_i, 1)
        await _phase(dut, capture=1)
        bits = await _shift(dut, guarded_frame(0, 0, False))
        await _phase(dut, update=1)
        assert bits[0] == 0, (
            f"REA-REQ-968: first bit out after CAPTURE of 0x{value:08X} is "
            f"{bits[0]}, expected the guard 0 (the 49-bit DR would put the "
            "value's bit0 here)")
        got = _word(bits[1:33])
        assert got == value, (
            f"REA-REQ-968: DR bits [32:1] = 0x{got:08X}, expected 0x{value:08X}")
    dut._log.info("REA-REQ-968 guard bit 0 and data placement PASS")


@cocotb.test()
@requires("REA-REQ-968")
async def test_guarded_write_and_read_decode(dut):
    """A guarded write frame produces exactly one reg_wr_en with the shifted
    addr and an ODD wdata; a guarded read frame pulses reg_rd_en with addr."""
    await _start(dut)
    seen = []

    async def _snoop():
        while True:
            await RisingEdge(dut.tck_i)
            await ReadOnly()
            if int(dut.reg_wr_en_o.value):
                seen.append(("w", int(dut.reg_addr_o.value), int(dut.reg_wdata_o.value)))
            if int(dut.reg_rd_en_o.value):
                seen.append(("r", int(dut.reg_addr_o.value), None))

    snoop = cocotb.start_soon(_snoop())
    await _phase(dut, capture=1)
    await _shift(dut, guarded_frame(0x0014, 0x0000_002B, True))
    await _phase(dut, update=1)
    await ClockCycles(dut.tck_i, 3)
    await _phase(dut, capture=1)
    await _shift(dut, guarded_frame(0x00D0, 0, False))
    await _phase(dut, update=1)
    await ClockCycles(dut.tck_i, 3)
    snoop.kill()
    assert seen == [("w", 0x0014, 0x0000_002B), ("r", 0x00D0, None)], (
        f"REA-REQ-968: bus events {seen}, expected one write of 0x2B to "
        "0x0014 then one read of 0x00D0")
    dut._log.info("REA-REQ-968 guarded write/read decode PASS")


if __name__ == "__main__":
    main()
