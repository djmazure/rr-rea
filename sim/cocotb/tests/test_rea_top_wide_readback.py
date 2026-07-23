# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""RTL-P1.91 — full-width paged REA capture readback."""

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
    "G_SAMPLE_W": 65,
    "G_DEPTH": 16,
    "G_TIMESTAMP_W": 0,
    "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_DATA_WORD_SEL = 0xCC
ADDR_START_PTR = 0xC8
ADDR_DATA_BASE = 0x100

CTRL_BIT_ARM = 0x01
STATUS_BIT_DONE = 0x04

SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS = 25.0
CAPTURED_VALUE = 0x1_89ABCDEF_01234567


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_top_wide_readback",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
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
    dut.probe_in.value = CAPTURED_VALUE
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


async def _jtag_write(dut, addr: int, data: int):
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, data, write=True), 49)
    await _update_phase(dut)


async def _jtag_read(dut, addr: int) -> int:
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)
    await ClockCycles(dut.tck, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


@cocotb.test()
@requires("REA-REQ-014", "REA-REQ-303")
async def test_wide_capture_data_pages_and_partial_word(dut):
    await _start_clocks(dut)
    await _reset(dut)

    assert await _jtag_read(dut, ADDR_DATA_WORD_SEL) == 0
    await _jtag_write(dut, ADDR_PRETRIG, 0)
    await _jtag_write(dut, ADDR_POSTTRIG, 2)
    await _jtag_write(dut, ADDR_TRIG_MODE, 1)
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    for _ in range(200):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_BIT_DONE:
            break
        await ClockCycles(dut.tck, 2)
    else:
        raise AssertionError("wide REA capture did not complete")

    start_ptr = await _jtag_read(dut, ADDR_START_PTR)
    sample_addr = ADDR_DATA_BASE + 4 * (start_ptr & 0xF)

    expected_words = [0x01234567, 0x89ABCDEF, 0x00000001]
    observed_words = []
    for word_index in range(3):
        await _jtag_write(dut, ADDR_DATA_WORD_SEL, word_index)
        assert await _jtag_read(dut, ADDR_DATA_WORD_SEL) == word_index
        observed_words.append(await _jtag_read(dut, sample_addr))
    assert observed_words == expected_words

    await _jtag_write(dut, ADDR_DATA_WORD_SEL, 0xFF)
    assert await _jtag_read(dut, ADDR_DATA_WORD_SEL) == 0xFF
    assert await _jtag_read(dut, sample_addr) == 0
    await _jtag_write(dut, ADDR_DATA_WORD_SEL, 0)
    assert await _jtag_read(dut, sample_addr) == 0x01234567


if __name__ == "__main__":
    main()
