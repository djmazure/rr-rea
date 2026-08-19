# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA full-depth window regression (RTL-T2.125)."""

from __future__ import annotations

from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles
from test_rea_timestamp_plane import (
    _FIX,
    _RTL_DIR,
    ADDR_CTRL,
    ADDR_POSTTRIG,
    ADDR_PRETRIG,
    ADDR_START_PTR,
    ADDR_STATUS,
    ADDR_TRIG_MASK,
    ADDR_TRIG_MODE,
    ADDR_TRIG_VALUE,
    GENERICS,
    _drive_probe_counter,
    _jtag_read,
    _jtag_write,
    _read_plane,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation
from sdk.cocotb_helpers import requires


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
                str(_RTL_DIR / "rr_rea_trust_core.vhd"),
                str(_RTL_DIR / "rr_rea_axis_window.vhd"),
                str(_RTL_DIR / "rr_rea_top.vhd"),
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


@cocotb.test()
@requires("REA-REQ-024")
async def test_rea_req_024_full_depth_keeps_oldest_pretrigger_cell(dut):
    depth = GENERICS["G_DEPTH"]
    pretrigger = 4
    posttrigger = depth - pretrigger - 1

    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_probe_counter(dut, 100_000))
    await ClockCycles(dut.sample_clk_i, 3 * depth)

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
        raise AssertionError("full-depth timestamp-plane capture did not complete")

    start_ptr = await _jtag_read(dut, ADDR_START_PTR) & (depth - 1)
    samples = [value & 0xFFF for value in await _read_plane(dut, start_ptr, 0, depth)]
    timestamps = [
        value & 0xFFFF for value in await _read_plane(dut, start_ptr, 1, depth)
    ]

    assert samples[pretrigger] == 0x1FF, (
        f"trigger cell overwritten: start={start_ptr}, samples={samples}"
    )
    for index in range(1, depth):
        assert (samples[index] - samples[index - 1]) & 0xFF == 1
        assert (timestamps[index] - timestamps[index - 1]) & 0xFFFF == 1


if __name__ == "__main__":
    main()
