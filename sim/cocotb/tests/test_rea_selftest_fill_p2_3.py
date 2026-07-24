# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.3 — selftest LFSR fill (REQ-850/851/853/854).

An accepted fill writes a deterministic LFSR pattern into the sample plane, sets
selftest_mode, bumps CAPTURE_EPOCH, then triggers the sweep. Read back over the
production DATA_BASE path it is word-exact against the host LFSR reference
(REQ-853/850); seed 0 substitutes the default (REQ-851); the timestamp plane is
never written (REQ-854). Golden values are the independent host LFSR per
ROUTERTL-002.
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

DEPTH = 32
SAMPLE_W = 12
TS_W = 16
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": DEPTH, "G_TIMESTAMP_W": TS_W, "G_NUM_CHAN": 1}

ADDR_CTRL           = 0x04
ADDR_STATUS         = 0x08
ADDR_SELFTEST_CTRL  = 0xDC
ADDR_SELFTEST_SEED  = 0xE0
ADDR_CAPTURE_EPOCH  = 0xEC
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE      = 0x100
CTRL_BIT_ARM   = 0x01
STATUS_BIT_CRC_VALID       = 1 << 4
STATUS_BIT_SELFTEST_BUSY   = 1 << 5
STATUS_BIT_SELFTEST_MODE   = 1 << 6
STATUS_BIT_SELFTEST_REFUSED = 1 << 7

DEFAULT_SEED = 0x52454108
SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS    = 25.0


def lfsr_cells(seed: int, depth: int, width: int) -> list[int]:
    """Host reference: cell i = pre-step LFSR word i (one page for width<=32),
    masked to `width`. taps 32,22,2,1; seed 0 → default."""
    x = seed & 0xFFFFFFFF or DEFAULT_SEED
    mask = (1 << width) - 1
    cells = []
    for _ in range(depth):
        cells.append(x & mask)  # width<=32 → one page/cell
        b = ((x >> 31) ^ (x >> 21) ^ (x >> 1) ^ x) & 1
        x = ((x >> 1) | (b << 31)) & 0xFFFFFFFF
    return cells


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


async def _trigger_fill(dut, ctrl_level: int):
    """SELFTEST_CTRL bit[0] is a toggle level — writing a NEW value edges the
    fill request. Returns the level written."""
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, ctrl_level)
    # fill (2*DEPTH cyc) + sweep + CDC — wait generously, then let crc_valid gate.
    for _ in range(400):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_BIT_CRC_VALID:
            break
        await ClockCycles(dut.tck_i, 4)
    else:
        raise AssertionError("fill+sweep never published crc_valid")


async def _read_sample_buffer(dut) -> list[int]:
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)
    cells = []
    for i in range(DEPTH):
        cells.append(await _jtag_read(dut, ADDR_DATA_BASE + 4 * i) & ((1 << SAMPLE_W) - 1))
    return cells


@cocotb.test()
@requires("REA-REQ-853", "REA-REQ-850")
async def test_fill_writes_lfsr_pattern_word_exact(dut):
    await _start_clocks(dut)
    await _reset(dut)

    epoch0 = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    SEED = 0x12345678
    await _jtag_write(dut, ADDR_SELFTEST_SEED, SEED)
    await _trigger_fill(dut, 1)

    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_BIT_SELFTEST_MODE, "REQ-853: selftest_mode must be set"
    assert status & STATUS_BIT_SELFTEST_BUSY == 0, "busy must have fallen"
    assert await _jtag_read(dut, ADDR_CAPTURE_EPOCH) == (epoch0 + 1) & 0xFFFFFFFF, \
        "REQ-853: accepted fill bumps CAPTURE_EPOCH"

    got = await _read_sample_buffer(dut)
    exp = lfsr_cells(SEED, DEPTH, SAMPLE_W)
    assert got == exp, (
        f"REQ-850/853: filled buffer not word-exact.\n got[:6]={[hex(v) for v in got[:6]]}\n"
        f" exp[:6]={[hex(v) for v in exp[:6]]}")
    dut._log.info("REA-REQ-850/853 PASS — LFSR fill word-exact via production readback")


@cocotb.test()
@requires("REA-REQ-851")
async def test_seed_zero_substituted(dut):
    await _start_clocks(dut)
    await _reset(dut)
    await _jtag_write(dut, ADDR_SELFTEST_SEED, 0x00000000)
    await _trigger_fill(dut, 1)
    got = await _read_sample_buffer(dut)
    exp = lfsr_cells(DEFAULT_SEED, DEPTH, SAMPLE_W)  # seed 0 → 0x52454108
    assert got == exp, "REQ-851: seed 0 must substitute the default 0x52454108"
    dut._log.info("REA-REQ-851 PASS — seed 0 substituted with default")


@cocotb.test()
@requires("REA-REQ-854")
async def test_timestamp_plane_not_filled(dut):
    await _start_clocks(dut)
    await _reset(dut)
    await _jtag_write(dut, ADDR_SELFTEST_SEED, 0x0BADF00D)
    await _trigger_fill(dut, 1)
    # The timestamp plane must NOT hold the sample LFSR pattern (it kept its
    # free-running counter values). Assert it differs from the sample pattern.
    exp_sample = lfsr_cells(0x0BADF00D, DEPTH, SAMPLE_W)
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 1)
    ts_cells = [await _jtag_read(dut, ADDR_DATA_BASE + 4 * i) & ((1 << TS_W) - 1)
                for i in range(DEPTH)]
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)
    assert ts_cells != [c & ((1 << TS_W) - 1) for c in exp_sample], \
        "REQ-854: timestamp plane must NOT contain the fill pattern"
    dut._log.info("REA-REQ-854 PASS — timestamp plane untouched by fill")


@cocotb.test()
@requires("REA-REQ-852")
async def test_fill_refused_while_armed(dut):
    await _start_clocks(dut)
    await _reset(dut)
    # Arm a capture (fires immediately with mask 0), then request a fill.
    await _jtag_write(dut, 0x14, DEPTH // 2 - 1)   # PRETRIG
    await _jtag_write(dut, 0x18, DEPTH // 2 - 1)   # POSTTRIG
    await _jtag_write(dut, 0x20, 0x1)              # TRIG_MODE value-match
    await _jtag_write(dut, 0x28, 0)                # TRIG_MASK 0
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)
    await ClockCycles(dut.tck_i, 4)                # armed, before done
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 1)  # request a fill while armed
    await ClockCycles(dut.sample_clk_i, 8)
    await ClockCycles(dut.tck_i, 4)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_BIT_SELFTEST_REFUSED, "REQ-852: fill while armed must be refused (sticky)"
    assert status & STATUS_BIT_SELFTEST_MODE == 0, "REQ-852: refused fill must not set selftest_mode"
    dut._log.info("REA-REQ-852 PASS — fill refused while armed, sticky, mode not set")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_selftest_fill_p2_3",
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
