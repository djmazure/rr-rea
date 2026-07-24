# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.2 part 2, increment 4 — timestamp-plane CRC sweep (REQ-804).

With G_TIMESTAMP_W>0, a second crc_sweep covers the timestamp plane and the
publication gates on BOTH planes. CRC_TS must equal zlib.crc32 of the port-B
timestamp readback (DATA_PLANE_SEL=1) — the same cross-path integrity check as
the sample plane, on the aligned timestamp buffer. (The G_TIMESTAMP_W=0 case —
CRC_TS reads 0 — is covered by test_rea_crc_integration_p2_2 / _epoch_status.)
"""
from __future__ import annotations

import struct
import sys as _sys
import zlib
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

DEPTH = 32
SAMPLE_W = 12
TS_W = 16
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": DEPTH, "G_TIMESTAMP_W": TS_W, "G_NUM_CHAN": 1}

ADDR_CTRL           = 0x04
ADDR_STATUS         = 0x08
ADDR_PRETRIG        = 0x14
ADDR_POSTTRIG       = 0x18
ADDR_TRIG_MODE      = 0x20
ADDR_TRIG_VALUE     = 0x24
ADDR_TRIG_MASK      = 0x28
ADDR_CRC_TS         = 0xE8
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE      = 0x100
CTRL_BIT_ARM   = 0x01
STATUS_BIT_DONE      = 1 << 2
STATUS_BIT_CRC_VALID = 1 << 4

SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS    = 25.0


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    for s in ("tdi_i", "capture_i", "shift_en_i", "update_i", "sel_i", "probe_i"):
        getattr(dut, s).value = 0
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


async def _drive_probe_counter(dut, cycles: int):
    cnt = 0
    for _ in range(cycles):
        dut.probe_i.value = (cnt & 0xFF) | 0x100
        await RisingEdge(dut.sample_clk_i)
        cnt = (cnt + 1) & 0xFF


@cocotb.test()
@requires("REA-REQ-804")
async def test_ts_plane_crc_matches_portb_readback(dut):
    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_probe_counter(dut, 100_000))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    await _jtag_write(dut, ADDR_PRETRIG, DEPTH // 2 - 1)
    await _jtag_write(dut, ADDR_POSTTRIG, DEPTH // 2 - 1)
    await _jtag_write(dut, ADDR_TRIG_MODE, 0x1)
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0)
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    for _ in range(200):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_BIT_DONE:
            break
        await ClockCycles(dut.tck_i, 4)
    else:
        raise AssertionError("capture never reached done")

    # crc_valid gates on BOTH planes — its rise proves the ts sweep completed too.
    for _ in range(400):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_BIT_CRC_VALID:
            break
        await ClockCycles(dut.tck_i, 4)
    else:
        raise AssertionError("REA-REQ-804: crc_valid never set (ts plane sweep stuck?)")

    crc_ts_on_chip = await _jtag_read(dut, ADDR_CRC_TS)

    # Read the physical timestamp plane over port B (DATA_PLANE_SEL=1), then
    # compute the canonical page-stream CRC with zlib. TS_W=16 → one page/cell.
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 1)
    pages = b""
    for i in range(DEPTH):
        cell = await _jtag_read(dut, ADDR_DATA_BASE + 4 * i) & ((1 << TS_W) - 1)
        pages += struct.pack("<I", cell)
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)  # restore
    crc_ts_ref = zlib.crc32(pages) & 0xFFFFFFFF

    assert crc_ts_on_chip == crc_ts_ref, (
        f"REA-REQ-804: timestamp-plane sweep CRC 0x{crc_ts_on_chip:08X} != "
        f"zlib of port-B readback 0x{crc_ts_ref:08X}"
    )
    dut._log.info(f"REA-REQ-804 PASS — timestamp plane cross-path CRC agrees, 0x{crc_ts_on_chip:08X}")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_crc_ts_plane_p2_2",
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
                f"{_RTL}/rr_rea_trust_core.vhd",
                f"{_RTL}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
