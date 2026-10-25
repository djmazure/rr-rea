# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.28 — unpipelined triggers at the capture FSM (REA-REQ-972, 974).

A host force (force_i, from CTRL[2]) and a cross-core trigger_i both bypass
the comparator pipeline: the trigger cell is the sample stored in the cycle
the pulse arrives. Every store counted by since_arm precedes that cell, so
PRETRIG_VALID must be min(stores since the arm, PRETRIG).

Before REA-P2.28 the every-cycle branch (no decimation, no qualification)
subtracted C_PIPE_STAGES (4 here) as if the trigger had come through the
comparator. A force soon after the arm then under-reported its pre-trigger
window and the host trimmed real samples. The decimated branch was already
exact, so the defect lived only where these tests drive it.

The oracle is cycle-exact and hard-coded: the bench pulses arm, waits N sample
edges, then pulses the trigger. N stores happened in between (the arm cycle's
own store is concurrent with the arm and is not counted).
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

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
DEPTH = 64
PRETRIG = 20
POSTTRIG = 6
# A pattern no sample matches: the probe counter is never 0xFFF here.
NEVER_VALUE, FULL_MASK = 0xFFF, 0xFFF
# N stores between the arm and the trigger -> the PRETRIG_VALID the core must
# report: min(N, PRETRIG). With the C_PIPE_STAGES (4) subtraction the
# pre-fix core reported max(N - 4, 0) for N - 4 < PRETRIG.
EXPECTED_PV = {0: 0, 1: 1, 3: 3, 4: 4, 5: 5, 10: 10, 19: 19, 23: 20, 30: 20}

_RTL_DIR = str(Path(__file__).resolve().parents[3] / "rtl")


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
        waves=False,
        simulator="nvc",
    )


def _set_if_present(dut, name: str, value: int) -> None:
    if hasattr(dut, name):
        getattr(dut, name).value = value


class Bench:
    """Free-running probe counter (never 0xFFF), a DPRAM model, and a log of
    every cycle trigger_o was high.

    Everything happens on the FALLING edge, half a cycle from the rising edge
    the FSM acts on: the probe changes, then the pending port-A write (we,
    addr, din as the next rising edge will see them) is logged."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.count = 0
        self.presented = 0   # the sample the next rising edge stores
        self.cells: dict[int, int] = {}
        self.trigger_o_cycles = 0

    async def run(self) -> None:
        dut = self.dut
        while True:
            await FallingEdge(dut.sample_clk_i)
            dut.probe_i.value = self.count
            self.presented = self.count
            self.count = (self.count + 1) % 0xFFF
            await ReadOnly()
            if int(dut.dpram_we_o.value):
                self.cells[int(dut.dpram_addr_o.value)] = int(dut.dpram_din_o.value)
            if int(dut.trigger_o.value):
                self.trigger_o_cycles += 1


async def _start(dut) -> Bench:
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    dut.sample_rst_i.value = 1
    for sig in ("probe_i", "arm_pulse_i", "reset_pulse_i", "trigger_i",
                "decim_ratio_i", "trig_mode_i"):
        getattr(dut, sig).value = 0
    _set_if_present(dut, "force_i", 0)
    dut.pretrig_len_i.value = PRETRIG
    dut.posttrig_len_i.value = POSTTRIG
    dut.trig_value_i.value = NEVER_VALUE
    dut.trig_mask_i.value = FULL_MASK
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    bench = Bench(dut)
    cocotb.start_soon(bench.run())
    # Fill the ring so every cell holds a written sample.
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)
    return bench


async def _capture(dut, bench: Bench, pulse_name: str, stores: int) -> dict:
    """Arm, let `stores` sample edges pass, pulse `pulse_name` for one cycle,
    then wait for done. Returns what the core reported."""
    pulse = getattr(dut, pulse_name)
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)          # E0: the arm lands
    dut.arm_pulse_i.value = 0
    await ClockCycles(dut.sample_clk_i, stores)  # E1..EN: N counted stores
    pulse.value = 1
    await RisingEdge(dut.sample_clk_i)          # E(N+1): the trigger cell
    landed_on = bench.presented                 # the sample E(N+1) stored
    pulse.value = 0
    await ReadOnly()
    fired = int(dut.triggered_o.value)
    trig_ptr = int(dut.trig_ptr_o.value)
    for _ in range(4 * POSTTRIG + 8):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.done_o.value):
            break
    return {
        "fired": fired,
        "landed_on": landed_on,
        "trig_ptr": trig_ptr,
        "done": int(dut.done_o.value),
        "start_ptr": int(dut.start_ptr_o.value),
        "pretrig_valid": int(dut.pretrig_valid_o.value),
        "forced": int(dut.forced_o.value) if hasattr(dut, "forced_o") else None,
    }


async def _sweep(dut, bench: Bench, pulse_name: str, req: str) -> list:
    results = []
    for stores, want_pv in EXPECTED_PV.items():
        got = await _capture(dut, bench, pulse_name, stores)
        assert got["fired"] == 1, (
            f"{req}: {pulse_name} pulse {stores} stores after the arm did not "
            "trigger the armed core in the cycle it arrived")
        assert got["done"] == 1, f"{req}: capture never reached done ({got})"
        assert bench.cells.get(got["trig_ptr"]) == got["landed_on"], (
            f"{req}: trigger cell holds {bench.cells.get(got['trig_ptr'])}, not "
            f"the sample {got['landed_on']} present when the {pulse_name} landed")
        assert got["start_ptr"] == (got["trig_ptr"] - PRETRIG) % DEPTH, (
            f"{req}: START_PTR {got['start_ptr']} is not trig_ptr - PRETRIG")
        assert got["pretrig_valid"] == want_pv, (
            f"{req}: PRETRIG_VALID={got['pretrig_valid']} after a {pulse_name} "
            f"{stores} stores after the arm, expected {want_pv} "
            f"(= min(stores since arm, PRETRIG={PRETRIG}))")
        results.append(got)
        await ClockCycles(dut.sample_clk_i, 3)
    return results


@cocotb.test()
@requires("REA-REQ-972")
async def test_rea_req_972_unpipelined_trigger_pretrig_valid(dut):
    """trigger_i N stores after the arm reports PRETRIG_VALID = min(N,
    PRETRIG), not N - C_PIPE_STAGES, and the window frames the trigger cell."""
    bench = await _start(dut)
    await _sweep(dut, bench, "trigger_i", "REA-REQ-972")


@cocotb.test()
@requires("REA-REQ-972", "REA-REQ-974")
async def test_rea_req_972_force_i_fires_armed_core_and_flags_it(dut):
    """force_i triggers an armed core in the cycle it arrives, with the same
    exact PRETRIG_VALID; forced_o reads 1 and trigger_o never pulses. A
    pattern capture afterwards reads forced_o = 0 (the arm cleared it)."""
    bench = await _start(dut)
    for got in await _sweep(dut, bench, "force_i", "REA-REQ-972"):
        assert got["forced"] == 1, f"REA-REQ-974: forced_o={got['forced']} after a force"
    assert bench.trigger_o_cycles == 0, (
        f"REA-REQ-974: a forced trigger pulsed trigger_o {bench.trigger_o_cycles} times")

    # Pattern capture: trigger on the next counter value well ahead.
    await ReadOnly()
    target = (bench.count + 40) % 0xFFF
    await RisingEdge(dut.sample_clk_i)
    dut.trig_value_i.value = target
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0
    await ReadOnly()
    assert int(dut.forced_o.value) == 0, "REA-REQ-974: arm did not clear forced_o"
    for _ in range(200):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.done_o.value):
            break
    assert int(dut.done_o.value) == 1, "REA-REQ-974: pattern capture never completed"
    assert bench.cells.get(int(dut.trig_ptr_o.value)) == target, (
        "REA-REQ-974: pattern capture's trigger cell is not the pattern")
    assert int(dut.forced_o.value) == 0, (
        "REA-REQ-974: forced_o=1 after a capture the pattern triggered")
    assert bench.trigger_o_cycles == 1, (
        f"REA-REQ-974: pattern fire pulsed trigger_o {bench.trigger_o_cycles} times, want 1")


if __name__ == "__main__":
    main()
