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
            "G_SAMPLE_W": 8,
            "G_DEPTH": 32,
            "G_TRIG_CONDS": 1,
            "G_TRIG_STAGES": 4,
        },
        waves=True,
        simulator="nvc",
    )


@cocotb.test()
@requires("REA-REQ-320", "REA-REQ-323", "REA-REQ-325")
async def test_narrow_configuration_has_one_derived_stage(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.arm_pulse_i.value = 0
    dut.reset_pulse_i.value = 0
    dut.trigger_i.value = 0
    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 2
    dut.trig_value_i.value = 0xA5
    dut.trig_mask_i.value = 0xFF
    dut.trig_mode_i.value = 1
    dut.decim_ratio_i.value = 0
    dut.seq_enable_i.value = 0
    dut.array_enable_i.value = 0
    dut.ext_trigger_i.value = 0
    dut.ext_enable_i.value = 0
    dut.ext_and_i.value = 0
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    await RisingEdge(dut.sample_clk_i)

    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0
    dut.probe_i.value = 0xA5
    await RisingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0
    await ReadOnly()
    assert int(dut.triggered_o.value) == 0

    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.triggered_o.value) == 1
    assert int(dut.trig_ptr_o.value) == 2


if __name__ == "__main__":
    main()
