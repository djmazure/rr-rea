# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm trigger-pipeline tests (RTL-P2.850, REA-REQ-302).

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

GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": 64,
}
DERIVED_PIPE_STAGES = 4
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


async def _start_clk(dut) -> None:
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())


def _set_if_present(dut, name: str, value: int) -> None:
    if hasattr(dut, name):
        getattr(dut, name).value = value


async def _reset(dut) -> None:
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.arm_pulse_i.value = 0
    dut.reset_pulse_i.value = 0
    dut.trigger_i.value = 0
    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 0
    dut.trig_value_i.value = 0
    dut.trig_mask_i.value = 0
    dut.trig_mode_i.value = 0
    dut.decim_ratio_i.value = 0
    _set_if_present(dut, "seq_enable_i", 0)
    _set_if_present(dut, "array_enable_i", 0)
    _set_if_present(dut, "ext_trigger_i", 0)
    _set_if_present(dut, "ext_enable_i", 0)
    _set_if_present(dut, "ext_and_i", 0)
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 1)


async def _pulse(sig, dut) -> None:
    sig.value = 1
    await RisingEdge(dut.sample_clk_i)
    sig.value = 0


class DpramMock:
    def __init__(self, dut):
        self.dut = dut
        self.cells: dict[int, int] = {}
        self._alive = True

    async def run(self) -> None:
        while self._alive:
            await RisingEdge(self.dut.sample_clk_i)
            if int(self.dut.dpram_we_o.value) == 1:
                self.cells[int(self.dut.dpram_addr_o.value)] = int(
                    self.dut.dpram_din_o.value
                )

    def stop(self) -> None:
        self._alive = False


@cocotb.test()
@requires("REA-REQ-302", "REA-REQ-320", "REA-REQ-322", "REA-REQ-325")
async def test_local_trigger_pipe_keeps_matched_sample_pointer(dut):
    await _start_clk(dut)
    await _reset(dut)

    dpram = DpramMock(dut)
    dpram_task = cocotb.start_soon(dpram.run())

    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 4
    dut.trig_value_i.value = 0x2A5
    dut.trig_mask_i.value = 0xFFF
    dut.trigger_i.value = 0

    await ClockCycles(dut.sample_clk_i, 10)
    await _pulse(dut.arm_pulse_i, dut)

    dut.probe_i.value = 0x111
    await RisingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0x2A5
    await RisingEdge(dut.sample_clk_i)

    dut.probe_i.value = 0x222
    for early_cycle in range(1, DERIVED_PIPE_STAGES):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        assert int(dut.triggered_o.value) == 0, (
            f"local trigger fired after only {early_cycle} derived stages"
        )

    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.triggered_o.value) == 1, "pipelined local trigger did not fire"
    assert int(dut.trigger_o.value) == 1, "pipelined local fire must pulse trigger_out"

    trig_ptr = int(dut.trig_ptr_o.value)
    assert dpram.cells.get(trig_ptr) == 0x2A5, (
        f"dpram[{trig_ptr}] should hold the matched trigger sample 0x2A5"
    )

    dpram.stop()
    del dpram_task


@cocotb.test()
@requires("REA-REQ-323", "REA-REQ-324")
async def test_trigger_in_bypasses_local_trigger_pipe_and_stays_remote(dut):
    await _start_clk(dut)
    await _reset(dut)

    dut.probe_i.value = 0
    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 2
    dut.trig_value_i.value = 0xFFF
    dut.trig_mask_i.value = 0xFFF
    dut.trigger_i.value = 0

    await ClockCycles(dut.sample_clk_i, 10)
    await _pulse(dut.arm_pulse_i, dut)
    await ClockCycles(dut.sample_clk_i, 4)

    fire_ptr = int(dut.wr_ptr_o.value)
    dut.trigger_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.trigger_i.value = 0
    await ReadOnly()

    assert int(dut.triggered_o.value) == 1, "trigger_in should fire immediately"
    assert int(dut.trigger_o.value) == 0, "remote trigger_in must not pulse trigger_out"
    delta = (int(dut.trig_ptr_o.value) - fire_ptr) & 0x3F
    assert delta <= 1, (
        f"trigger_in trig_ptr delta {delta} should be immediate, not pipe-delayed"
    )


if __name__ == "__main__":
    main()
