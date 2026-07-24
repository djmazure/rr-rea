# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.2 part 2, increment 1 — CAPTURE_EPOCH counter + v0.8 STATUS/readback.

REA-REQ-807: CAPTURE_EPOCH (0xEC) increments on exactly an accepted arm or soft
reset, read over JTAG. In this increment the CRC registers + crc_valid are inert
(0) until the sweep lands (P2.2p2-sweep); selftest bits are 0 until P2.3.
Expected values are hard-coded per ROUTERTL-002 (deterministic counter values).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parents[1])
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": 256, "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1}

ADDR_CTRL          = 0x04
ADDR_STATUS        = 0x08
ADDR_CRC_SAMPLE    = 0xE4
ADDR_CRC_TS        = 0xE8
ADDR_CAPTURE_EPOCH = 0xEC
CTRL_BIT_ARM   = 0x01
CTRL_BIT_RESET = 0x02

SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS    = 25.0


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    dut.probe_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)


async def _capture_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 1
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    from cocotb.triggers import NextTimeStep
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.update_i.value = 0
    dut.shift_en_i.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi_i.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo_o.value) & 1) << i
        await RisingEdge(dut.tck_i)
    dut.shift_en_i.value = 0
    return out


async def _update_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 1
    await RisingEdge(dut.tck_i)
    dut.update_i.value = 0
    dut.sel_i.value = 0


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
    await ClockCycles(dut.tck_i, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


async def _pulse_ctrl(dut, bit: int):
    """CTRL is write-toggle: writing `bit` XORs it, producing one toggle → one
    sample-domain pulse. Wait for the toggle to cross to the sample domain,
    bump the epoch, and re-cross the counter to the jtag domain before reading."""
    await _jtag_write(dut, ADDR_CTRL, bit)
    await ClockCycles(dut.sample_clk_i, 12)  # toggle→pulse + counter + word sync
    await ClockCycles(dut.tck_i, 4)


@cocotb.test()
@requires("REA-REQ-807")
async def test_capture_epoch_increments_on_arm_and_reset(dut):
    await _start_clocks(dut)
    await _reset(dut)

    # Post-reset: epoch 0, CRC regs inert, v0.8 STATUS bits clear.
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == 0
    assert await _jtag_read(dut, ADDR_CRC_SAMPLE) == 0
    assert await _jtag_read(dut, ADDR_CRC_TS) == 0
    status = await _jtag_read(dut, ADDR_STATUS)
    assert (status >> 4) & 0xF == 0, f"STATUS[7:4] must be 0 in P2.2, got {status:#010x}"

    # Each accepted arm bumps the epoch by exactly one. Cross-check the
    # JTAG-read value against the internal sample-domain counter capture_epoch_r
    # (proves the sample→jtag word sync carried it faithfully).
    await _pulse_ctrl(dut, CTRL_BIT_ARM)
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == 1
    assert int(dut.capture_epoch_r.value) == 1
    await _pulse_ctrl(dut, CTRL_BIT_ARM)
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == 2
    assert int(dut.capture_epoch_r.value) == 2

    # A soft reset (CTRL bit[1]) also bumps it.
    await _pulse_ctrl(dut, CTRL_BIT_RESET)
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == 3
    assert int(dut.capture_epoch_r.value) == 3

    # A plain read does NOT move it.
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == 3

    dut._log.info("REA-REQ-807 PASS — CAPTURE_EPOCH bumps on arm/reset, stable otherwise")


@cocotb.test()
@requires("REA-REQ-010")
async def test_selftest_ctrl_seed_round_trip(dut):
    """SELFTEST_CTRL bit[0] and SELFTEST_SEED are RW and round-trip (REA-P2.3
    register surface; the fill FSM that consumes them lands next)."""
    ADDR_SELFTEST_CTRL = 0xDC
    ADDR_SELFTEST_SEED = 0xE0
    await _start_clocks(dut)
    await _reset(dut)

    await _jtag_write(dut, ADDR_SELFTEST_SEED, 0xDEADBEEF)
    assert await _jtag_read(dut, ADDR_SELFTEST_SEED) == 0xDEADBEEF
    await _jtag_write(dut, ADDR_SELFTEST_SEED, 0x00000000)
    assert await _jtag_read(dut, ADDR_SELFTEST_SEED) == 0x00000000

    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 0x1)
    assert await _jtag_read(dut, ADDR_SELFTEST_CTRL) & 0x1 == 1
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 0x0)
    assert await _jtag_read(dut, ADDR_SELFTEST_CTRL) & 0x1 == 0
    dut._log.info("REA-REQ-010 PASS — SELFTEST_CTRL/SEED round-trip")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_epoch_status_p2_2",
        custom_libraries={
            "work": [
                f"{_RTL}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL}/rr_rea_dpram.vhd",
                f"{_RTL}/rr_rea_capture_fsm.vhd",
                f"{_RTL}/rr_rea_regbank.vhd",
                f"{_RTL}/rr_rea_cdc.vhd",
                f"{_RTL}/rr_rea_jtag_iface.vhd",
                f"{_RTL}/rr_rea_crc_sweep.vhd",
                f"{_RTL}/rr_rea_fill_fsm.vhd",
                f"{_RTL}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
