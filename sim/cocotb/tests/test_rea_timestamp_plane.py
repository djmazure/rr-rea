# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA v0.7 timestamp-plane effect test (RTL-T2.123)."""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles

_SIM_ROOT = str(Path(__file__).resolve().parents[2])
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)

from test_rea_top import (
    _drive_probe_counter,
    _jtag_read,
    _jtag_write,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation
from sdk.cocotb_helpers import requires

GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": 16,
    "G_TIMESTAMP_W": 16,
    "G_NUM_CHAN": 1,
}
_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_START_PTR = 0xC8
ADDR_DATA_WORD_SEL = 0xCC
ADDR_FEATURES = 0xD0
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE = 0x100


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module=Path(__file__).stem,
        custom_libraries={
            "work": [
                str(_RTL_DIR / "rr_rea_pkg.vhd"),
                str(_FIX / "rr_rea_build_id_stub.vhd"),
                str(_RTL_DIR / "rr_rea_dpram.vhd"),
                str(_RTL_DIR / "rr_rea_capture_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_regbank.vhd"),
                str(_RTL_DIR / "rr_rea_cdc.vhd"),
                str(_RTL_DIR / "rr_rea_jtag_iface.vhd"),
                str(_RTL_DIR / "rr_rea_crc_sweep.vhd"),
                str(_RTL_DIR / "rr_rea_fill_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_top.vhd"),
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


async def _read_plane(dut, start_ptr: int, plane: int, count: int) -> list[int]:
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, plane)
    await _jtag_write(dut, ADDR_DATA_WORD_SEL, 0)
    values = []
    for offset in range(count):
        cell = (start_ptr + offset) & (GENERICS["G_DEPTH"] - 1)
        values.append(await _jtag_read(dut, ADDR_DATA_BASE + 4 * cell))
    return values


@cocotb.test()
@requires("REA-REQ-024")
async def test_rea_req_024_timestamp_alignment_across_wrap_and_trigger(dut):
    depth = GENERICS["G_DEPTH"]
    # This deterministic trigger places START_PTR near the physical ring end,
    # so the eight-cell logical window crosses address wrap. RTL-T2.125 tracks
    # the independently discovered near-full-window comparator-pipeline overrun.
    pretrigger = 4
    posttrigger = 3
    capture_len = pretrigger + posttrigger + 1

    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_probe_counter(dut, 100_000))
    await ClockCycles(dut.sample_clk_i, 3 * depth)

    assert await _jtag_read(dut, ADDR_FEATURES) & (1 << 18)
    await _jtag_write(dut, ADDR_PRETRIG, pretrigger)
    await _jtag_write(dut, ADDR_POSTTRIG, posttrigger)
    await _jtag_write(dut, ADDR_TRIG_MODE, 1)
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0x1FF)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0xFFF)
    await _jtag_write(dut, ADDR_CTRL, 1)

    for _ in range(200):
        if await _jtag_read(dut, ADDR_STATUS) & 0x04:
            break
        await ClockCycles(dut.tck_i, 4)
    else:
        raise AssertionError("timestamp-plane capture did not complete")

    start_ptr = await _jtag_read(dut, ADDR_START_PTR) & (depth - 1)
    assert start_ptr + capture_len > depth, "stimulus did not cross physical ring wrap"
    samples = [
        value & 0xFFF
        for value in await _read_plane(dut, start_ptr, 0, capture_len)
    ]
    timestamps = [
        value & 0xFFFF
        for value in await _read_plane(dut, start_ptr, 1, capture_len)
    ]

    assert samples[pretrigger] == 0x1FF
    offsets = {
        ((sample & 0xFF) - (timestamp & 0xFF)) & 0xFF
        for sample, timestamp in zip(samples, timestamps)
    }
    assert len(offsets) == 1, "sample/timestamp cells lost one-to-one alignment"
    for index in range(1, capture_len):
        assert (samples[index] - samples[index - 1]) & 0xFF == 1
        assert (timestamps[index] - timestamps[index - 1]) & 0xFFFF == 1

    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)
    dut._log.info(
        f"REA-REQ-024 PASS — {capture_len} aligned cells from START_PTR={start_ptr}, "
        f"trigger at logical index {pretrigger}"
    )


if __name__ == "__main__":
    main()
