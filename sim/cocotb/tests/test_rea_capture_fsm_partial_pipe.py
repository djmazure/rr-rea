# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT

from __future__ import annotations

import sys as _sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_sim = str(Path(__file__).resolve().parents[2])
if _sim not in _sys.path:
    _sys.path.insert(0, _sim)
del _sim

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

SAMPLE_W = 33
TRIG_CONDS = 3
PIPE_STAGES = 7
_RTL_DIR = str(
    Path(__file__).resolve().parents[3] / "rtl"
)


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module=Path(__file__).stem,
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
            ],
        },
        generics={
            "G_SAMPLE_W": SAMPLE_W,
            "G_DEPTH": 64,
            "G_TRIG_CONDS": TRIG_CONDS,
        },
        waves=True,
        simulator="nvc",
    )


def _pack(values: list[int], width: int) -> int:
    result = 0
    for index, value in enumerate(values):
        result |= value << (index * width)
    return result


async def _reset(dut) -> None:
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.trigger_in.value = 0
    dut.pretrig_len_in.value = 0
    dut.posttrig_len_in.value = 2
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0
    dut.trig_mode_in.value = 0
    dut.decim_ratio_in.value = 0
    dut.seq_enable_in.value = 0
    dut.array_enable_in.value = 0
    dut.ext_trigger_in.value = 0
    dut.ext_enable_in.value = 0
    dut.ext_and_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await RisingEdge(dut.sample_clk)


@cocotb.test()
@requires("REA-REQ-320", "REA-REQ-321", "REA-REQ-325", "REA-REQ-326")
async def test_partial_top_slice_and_three_way_reduction(dut):
    cocotb.start_soon(Clock(dut.sample_clk, 8.0, unit="ns").start())
    await _reset(dut)

    dut.array_enable_in.value = 1
    dut.cond_values_in.value = _pack([1 << 32, 0x10, 0x2000], SAMPLE_W)
    dut.cond_masks_in.value = _pack([1 << 32, 0xFF, 0xFF00], SAMPLE_W)
    dut.cond_ops_in.value = _pack([0, 3, 2], 4)
    dut.cond_valid_in.value = 0b111
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0

    dut.probe_in.value = (1 << 32) | 0x1011
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0
    for completed_stage in range(1, PIPE_STAGES):
        await RisingEdge(dut.sample_clk)
        await ReadOnly()
        assert int(dut.triggered.value) == 0, (
            f"33-bit/3-condition trigger fired after {completed_stage} stages"
        )

    await RisingEdge(dut.sample_clk)
    await ReadOnly()
    assert int(dut.triggered.value) == 1


@cocotb.test()
@requires("REA-REQ-325", "REA-REQ-326")
async def test_rise_on_bit_32_survives_partial_slice(dut):
    cocotb.start_soon(Clock(dut.sample_clk, 8.0, unit="ns").start())
    await _reset(dut)

    dut.trig_mask_in.value = 1 << 32
    dut.trig_mode_in.value = 1 | (4 << 4)
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0
    dut.probe_in.value = 0
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 1 << 32
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0
    await ClockCycles(dut.sample_clk, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered.value) == 1


if __name__ == "__main__":
    main()
