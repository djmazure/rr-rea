# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.32 — reset-armed comparator array + storage qualifier at the capture
FSM (REA-REQ-977, 978).

Tests rr_rea_capture_fsm directly with G_ARM_AT_RESET = True, comparator-array
mode (G_RESET_TRIG_MODE = 1 << 2, G_RESET_COND_CFG, G_RESET_COND_VAL), and
storage qualification (G_QUAL_CONDS = 1, G_RESET_QUAL_MODE = 1,
G_RESET_QUAL_CFG, G_RESET_QUAL_VAL) while ALL configuration input ports on the
entity are tied to zero and arm_pulse_i is never asserted.

This proves that the FSM's own synthesis-time G_RESET_* generics initialize the
armed state, window bounds, comparator-array slot 0, and storage-qualifier slot
0 directly on sample_rst_i without relying on rr_rea_regbank or TCK.
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

SAMPLE_W = 12
DEPTH = 64
RESET_PRETRIG = 4
RESET_POSTTRIG = 4
# Comparator-array slot 0: op=EQ (0), width=8 bits, lsb=0, value=0x24.
RESET_COND_CFG = (0 << 24) | (8 << 16) | (0 << 8)
RESET_COND_VAL = 0x24
# Qualifier slot 0: op=EQ (0), width=4 bits, lsb=8, value=0x5 (qualifies 0x5XX only).
RESET_QUAL_CFG = (0 << 24) | (4 << 16) | (8 << 8)
RESET_QUAL_VAL = 0x5

GENERICS = {
    "G_SAMPLE_W": SAMPLE_W,
    "G_DEPTH": DEPTH,
    "G_TRIG_CONDS": 2,
    "G_QUAL_CONDS": 1,
    "G_ARM_AT_RESET": True,
    "G_RESET_PRETRIG": RESET_PRETRIG,
    "G_RESET_POSTTRIG": RESET_POSTTRIG,
    "G_RESET_TRIG_MODE": 1 << 2,  # C_TRIG_MODE_BIT_ARRAY_EN
    "G_RESET_COND_CFG": RESET_COND_CFG,
    "G_RESET_COND_VAL": RESET_COND_VAL,
    "G_RESET_QUAL_MODE": 1,       # C_QUAL_MODE_BIT_ENABLE
    "G_RESET_QUAL_CFG": RESET_QUAL_CFG,
    "G_RESET_QUAL_VAL": RESET_QUAL_VAL,
}

# Only qualified samples (upper nibble 0x5) are stored around trigger 0x524:
EXPECTED_QUAL_BOOT_WINDOW = [
    0x520, 0x521, 0x522, 0x523,
    0x524,
    0x525, 0x526, 0x527, 0x528,
]

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


def _tie_inputs_zero(dut) -> None:
    for sig in (
        "probe_i",
        "arm_pulse_i",
        "reset_pulse_i",
        "trigger_i",
        "force_i",
        "pretrig_len_i",
        "posttrig_len_i",
        "trig_value_i",
        "trig_mask_i",
        "trig_mode_i",
        "decim_ratio_i",
        "seq_enable_i",
        "seq_values_i",
        "seq_masks_i",
        "seq_counts_i",
        "array_enable_i",
        "cond_values_i",
        "cond_masks_i",
        "cond_ops_i",
        "cond_valid_i",
        "ext_trigger_i",
        "ext_enable_i",
        "ext_and_i",
        "qual_enable_i",
        "qual_or_i",
        "qual_values_i",
        "qual_masks_i",
        "qual_ops_i",
        "qual_valid_i",
    ):
        getattr(dut, sig).value = 0


async def _reset_with_inputs_tied_zero(dut) -> None:
    _tie_inputs_zero(dut)
    dut.sample_rst_i.value = 1
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    await ReadOnly()


@cocotb.test()
@requires("REA-REQ-977", "REA-REQ-978")
async def test_fsm_arm_at_reset_with_cond_and_qual_and_zero_inputs(dut):
    """Out of sample_rst_i with all config inputs tied 0, the FSM is armed
    (armed_o=1, armed_at_reset_o=1), filters unqualified samples via
    G_RESET_QUAL_*, triggers on G_RESET_COND_* slot 0, and freezes the exact
    qualified window."""
    await _reset_with_inputs_tied_zero(dut)

    armed = int(dut.armed_o.value)
    assert armed == 1, (
        f"REA-REQ-977: armed_o={armed} out of sample_rst_i with "
        "G_ARM_AT_RESET=true, want 1 (the FSM did not leave reset armed)")
    assert int(dut.armed_at_reset_o.value) == 1
    assert int(dut.triggered_o.value) == 0
    assert int(dut.done_o.value) == 0

    cells: dict[int, int] = {}

    async def _step_probe(val: int) -> None:
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = val
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.dpram_we_o.value) == 1:
            cells[int(dut.dpram_addr_o.value)] = int(dut.dpram_din_o.value)

    # Interleave unqualified samples (0x0AA has upper nibble 0x0 != 0x5 so it
    # must NOT be stored) with qualified samples (0x518..0x530).
    for k in range(0x18, 0x31):
        await _step_probe(0x0AA)  # unqualified decoy
        await _step_probe(0x500 | k)

    for _ in range(12):
        await _step_probe(0x000)

    assert int(dut.done_o.value) == 1
    assert int(dut.triggered_o.value) == 1
    assert int(dut.armed_o.value) == 0
    assert int(dut.armed_at_reset_o.value) == 1
    assert int(dut.pretrig_valid_o.value) == RESET_PRETRIG

    start_ptr = int(dut.start_ptr_o.value)
    window = [cells[(start_ptr + i) % DEPTH] for i in range(9)]
    assert window == EXPECTED_QUAL_BOOT_WINDOW, (
        f"REA-REQ-977: qualified boot window {window} != {EXPECTED_QUAL_BOOT_WINDOW}"
    )


@cocotb.test()
@requires("REA-REQ-978")
async def test_fsm_armed_at_reset_cleared_by_reset_pulse_restored_by_sample_rst(dut):
    """armed_at_reset_o is set by sample_rst_i, cleared by reset_pulse_i (the
    host's CTRL[1]), and set again by the next hard sample_rst_i. Split out of
    the boot-capture test above so that test proves only the reset arm."""
    await _reset_with_inputs_tied_zero(dut)
    assert int(dut.armed_o.value) == 1
    assert int(dut.armed_at_reset_o.value) == 1

    # Complete the boot capture first, as before the split, so the soft reset
    # below is shown clearing a DONE capture, not an idle one.
    for k in range(0x18, 0x31):
        for val in (0x0AA, 0x500 | k):
            await FallingEdge(dut.sample_clk_i)
            dut.probe_i.value = val
    for _ in range(12):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x000
    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.done_o.value) == 1
    assert int(dut.armed_at_reset_o.value) == 1

    # Soft reset clears armed_at_reset_o; another hard sample_rst_i re-arms at reset.
    await FallingEdge(dut.sample_clk_i)
    dut.reset_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    await FallingEdge(dut.sample_clk_i)
    dut.reset_pulse_i.value = 0
    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert int(dut.armed_at_reset_o.value) == 0
    assert int(dut.done_o.value) == 0

    await FallingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    await FallingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    await ReadOnly()
    assert int(dut.armed_o.value) == 1
    assert int(dut.armed_at_reset_o.value) == 1


if __name__ == "__main__":
    main()
