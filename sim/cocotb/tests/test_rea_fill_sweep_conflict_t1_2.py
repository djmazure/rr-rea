# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T1.2 — port-A conflict: a fill accepted during an active sweep.

A fill-triggered validation sweep runs with armed=0, so before the fix the fill
FSM (which only refused on armed/triggered) would ACCEPT a second fill issued
mid-sweep. That fill wins the port-A arbiter (dpram_addr_a <= fill_addr_r when
fill_busy) and hijacks the sweep's read address, corrupting the CRC over the
wrong cells while still able to publish crc_valid.

The fix refuses a fill while sweep_busy (REQ-852). This test:
  * runs a continuous port-A single-owner monitor — fill_we and sweep_owns_a
    must NEVER both be high (the invariant the defect violates);
  * issues a second fill *during* the first fill's sweep and asserts it is
    REFUSED (sticky selftest_refused), selftest_mode is untouched, and the
    buffer is still fill-1's word-exact LFSR pattern (uncorrupted).

DEPTH is deliberately large: the sweep must outlast a JTAG write so the second
fill request genuinely lands mid-sweep (a small DEPTH sweep finishes before the
~49-tck write completes and cannot exercise the hazard).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Event, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parents[1])
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

DEPTH = 512          # sweep (~DEPTH sample cyc) must outlast a ~49-tck JTAG write
SAMPLE_W = 12
TS_W = 16
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": DEPTH, "G_TIMESTAMP_W": TS_W, "G_NUM_CHAN": 1}

ADDR_STATUS         = 0x08
ADDR_SELFTEST_CTRL  = 0xDC
ADDR_SELFTEST_SEED  = 0xE0
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE      = 0x100
STATUS_BIT_CRC_VALID        = 1 << 4
STATUS_BIT_SELFTEST_MODE    = 1 << 6
STATUS_BIT_SELFTEST_REFUSED = 1 << 7

SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS    = 25.0
DEFAULT_SEED = 0x52454108


def lfsr_cells(seed: int, depth: int, width: int) -> list[int]:
    x = seed & 0xFFFFFFFF or DEFAULT_SEED
    mask = (1 << width) - 1
    cells = []
    for _ in range(depth):
        cells.append(x & mask)
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


async def _read_sample_buffer(dut) -> list[int]:
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)
    cells = []
    for i in range(DEPTH):
        cells.append(await _jtag_read(dut, ADDR_DATA_BASE + 4 * i) & ((1 << SAMPLE_W) - 1))
    return cells


async def _port_a_owner_monitor(dut, stop: Event):
    """The invariant REA-T1.2 violates: the free-running capture write and the
    fill write and the sweep read never contend for port A at once. fill_we and
    sweep_owns_a both high is the exact conflict; assert it never happens."""
    while not stop.is_set():
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        fill_we = int(dut.fill_we.value)
        sweep_owns = int(dut.sweep_owns_a.value)
        assert not (fill_we and sweep_owns), (
            "REA-T1.2: fill_we and sweep_owns_a both high — a fill is writing "
            "port A while the sweep reads it (CRC corruption)."
        )


@cocotb.test()
@requires("REA-REQ-852")
async def test_fill_during_sweep_is_refused(dut):
    await _start_clocks(dut)
    await _reset(dut)

    stop = Event()
    cocotb.start_soon(_port_a_owner_monitor(dut, stop))

    SEED = 0x12345678
    await _jtag_write(dut, ADDR_SELFTEST_SEED, SEED)
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 1)   # toggle → fill-1 request

    # Wait until fill-1's validation sweep is actually running.
    for _ in range(40000):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.sweep_busy.value) == 1:
            break
    else:
        raise AssertionError("fill-1's sweep never started")

    # Leave the ReadOnly phase before driving the JTAG lines again.
    await RisingEdge(dut.tck_i)
    # Issue fill-2 mid-sweep (SELFTEST_CTRL 1→0 is a fresh toggle edge). DEPTH is
    # large enough that the sweep is still busy when this write's UPDATE lands.
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 0)

    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_BIT_SELFTEST_REFUSED, (
        "REA-T1.2 (REQ-852): a fill issued during an active sweep must be REFUSED"
    )
    assert status & STATUS_BIT_SELFTEST_MODE, (
        "the in-flight fill-1's selftest_mode must be untouched by the refused fill-2"
    )

    # Let fill-1's sweep publish, monitor still armed across the whole window.
    for _ in range(40000):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.sweep_busy.value) == 0:
            break
    await ClockCycles(dut.tck_i, 20)
    stop.set()

    # The buffer must still be fill-1's word-exact pattern (fill-2 never wrote).
    got = await _read_sample_buffer(dut)
    exp = lfsr_cells(SEED, DEPTH, SAMPLE_W)
    assert got == exp, (
        "REA-T1.2: fill buffer corrupted — the refused mid-sweep fill must not "
        "have touched port A"
    )
    dut._log.info("REA-T1.2 PASS — mid-sweep fill refused, port-A exclusion held, CRC intact")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_fill_sweep_conflict_t1_2",
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
