# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT

from __future__ import annotations

import sys as _sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
_sim = str(Path(__file__).resolve().parents[2])
if _sim not in _sys.path:
    _sys.path.insert(0, _sim)
del _tb, _sim

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

SAMPLE_W = 40
TRIG_CONDS = 8
PIPE_STAGES = 8
SAMPLE_MASK = (1 << SAMPLE_W) - 1
GENERICS = {
    "G_SAMPLE_W": SAMPLE_W,
    "G_DEPTH": 128,
    "G_TRIG_CONDS": TRIG_CONDS,
    "G_TRIG_STAGES": 3,
}
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
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


async def _start_clock(dut) -> None:
    cocotb.start_soon(Clock(dut.sample_clk, 8.0, unit="ns").start())


async def _reset(dut) -> None:
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.trigger_in.value = 0
    dut.pretrig_len_in.value = 0
    dut.posttrig_len_in.value = 4
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


async def _arm(dut, *, target: int, operation: int) -> None:
    dut.trig_value_in.value = target
    dut.trig_mask_in.value = SAMPLE_MASK
    dut.trig_mode_in.value = 1 | (operation << 4)
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0


@cocotb.test()
@requires(
    "REA-REQ-320",
    "REA-REQ-321",
    "REA-REQ-322",
    "REA-REQ-325",
    "REA-REQ-326",
)
async def test_wide_gt_uses_exact_derived_latency_and_pointer_tag(dut):
    await _start_clock(dut)
    await _reset(dut)

    target = 0x0100_0000_00
    matching_sample = 0x0100_0000_01
    await _arm(dut, target=target, operation=3)

    dut.probe_in.value = target
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = matching_sample
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0

    for completed_stage in range(1, PIPE_STAGES):
        await RisingEdge(dut.sample_clk)
        await ReadOnly()
        assert int(dut.triggered.value) == 0, (
            f"40-bit decision fired after {completed_stage} stages; expected 8"
        )

    await RisingEdge(dut.sample_clk)
    await ReadOnly()
    assert int(dut.triggered.value) == 1
    assert int(dut.trigger_out.value) == 1
    assert int(dut.trig_ptr_out.value) == 3


@cocotb.test()
@requires("REA-REQ-325", "REA-REQ-326")
async def test_wide_lt_crosses_32_bit_boundary_without_order_reversal(dut):
    await _start_clock(dut)
    await _reset(dut)

    target = 0x0100_0000_00
    await _arm(dut, target=target, operation=2)
    dut.probe_in.value = 0x00FF_FFFF_FF
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0x0200_0000_00
    await ClockCycles(dut.sample_clk, PIPE_STAGES + 1)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, (
        "LT token combine reversed significance across the 32-bit boundary"
    )


if __name__ == "__main__":
    main()
