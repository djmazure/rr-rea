# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_top derived trigger-pipeline integration test (RTL-P3.1167).

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from test_rea_top import (  # noqa: E402
    ADDR_CTRL,
    ADDR_DATA_BASE,
    ADDR_POSTTRIG,
    ADDR_PRETRIG,
    ADDR_START_PTR,
    ADDR_STATUS,
    ADDR_TRIG_MASK,
    ADDR_TRIG_MODE,
    ADDR_TRIG_VALUE,
    CTRL_BIT_ARM,
    STATUS_BIT_DONE,
    _jtag_read,
    _jtag_write,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

DERIVED_PIPE_STAGES = 4
GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": 64,
    "G_TIMESTAMP_W": 0,
    "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_top_input_pipe",
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


async def _wait_armed(dut) -> None:
    for _ in range(50):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.armed_sclk.value) == 1:
            return
    assert False, "REA-REQ-301 setup failed: arm pulse never reached sample_clk"


async def _wait_done(dut) -> None:
    for _ in range(50):
        status = await _jtag_read(dut, ADDR_STATUS)
        if (status & STATUS_BIT_DONE) != 0:
            return
        await ClockCycles(dut.tck_i, 2)
    assert False, "REA-REQ-301 failed: capture did not complete after trigger"


@cocotb.test()
@requires(
    "REA-REQ-301",
    "REA-REQ-320",
    "REA-REQ-322",
    "REA-REQ-323",
    "REA-REQ-325",
)
async def test_derived_pipe_delays_trigger_and_preserves_sample(dut):
    pulse_value = 0x3A5

    await _start_clocks(dut)
    await _reset(dut)

    await _jtag_write(dut, ADDR_PRETRIG, 0)
    await _jtag_write(dut, ADDR_POSTTRIG, 4)
    await _jtag_write(dut, ADDR_TRIG_MODE, 0x0000_0001)
    await _jtag_write(dut, ADDR_TRIG_VALUE, pulse_value)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0x0000_0FFF)
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)
    await _wait_armed(dut)
    await NextTimeStep()

    dut.probe_i.value = pulse_value
    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.triggered_sclk.value) == 0, (
        "REA-REQ-320 failed: trigger fired before the first derived stage"
    )

    await NextTimeStep()
    dut.probe_i.value = 0
    for early_cycle in range(1, DERIVED_PIPE_STAGES):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        assert int(dut.triggered_sclk.value) == 0, (
            "REA-REQ-320 failed: trigger fired after "
            f"{early_cycle} stages, expected {DERIVED_PIPE_STAGES}"
        )

    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.triggered_sclk.value) == 1, (
        "REA-REQ-320 failed: one-cycle probe pulse did not emerge after "
        f"{DERIVED_PIPE_STAGES} derived stages"
    )

    await NextTimeStep()
    await _wait_done(dut)
    start_ptr = await _jtag_read(dut, ADDR_START_PTR)
    captured = await _jtag_read(dut, ADDR_DATA_BASE + 4 * (start_ptr & 0x3F))
    assert (captured & 0xFFF) == pulse_value, (
        "REA-REQ-322 failed: captured trigger sample did not match the "
        f"pointer-tagged probe (got 0x{captured & 0xFFF:03X})"
    )


if __name__ == "__main__":
    main()
