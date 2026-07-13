# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — wide multi-stage sequencer comparator tests
# (RTL-P3.691, sibling of RTL-P2.658b).
#
# Readiness guard for the sequencer width contract: the per-stage
# value_a/mask_a comparators must cover the FULL G_SAMPLE_W, not 32
# bits. The sequencer's JTAG register slots aren't built yet, but the
# capture-FSM already carries the per-stage fields full-width — this
# pins that so a future refactor (or the eventual regbank slots) can't
# silently reintroduce the 32-bit cap on a stage comparator.
#
# Elaborated at G_SAMPLE_W=64, G_TRIG_STAGES=2. The adversarial case
# mirrors test_rea_capture_fsm_wide: a per-stage value matching the low
# 32 bits but differing in the upper word must NOT match that stage.
#
# Run via:  rr sim run --ip <rea-dir> test_rea_capture_fsm_seq_wide

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

G_SAMPLE_W = 64
NUM_STAGES = 2
G_TRIG_CONDS = 4
GENERICS = {
    "G_SAMPLE_W": G_SAMPLE_W,
    "G_DEPTH": 256,
    "G_TRIG_STAGES": NUM_STAGES,
    "G_TRIG_CONDS": G_TRIG_CONDS,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[4] / "ip" / "routertl" / "rea" / "rtl")

FULL_MASK = (1 << G_SAMPLE_W) - 1
PIPE_STAGES = (G_SAMPLE_W + 7) // 8 + (G_TRIG_CONDS - 1).bit_length()


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_seq_wide",
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


CLK_PERIOD_NS = 8.0


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.sample_clk, CLK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.pretrig_len_in.value = 0
    dut.posttrig_len_in.value = 0
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0
    dut.decim_ratio_in.value = 0
    dut.trigger_in.value = 0
    dut.seq_enable_in.value = 0
    dut.seq_values_in.value = 0
    dut.seq_masks_in.value = 0
    dut.seq_counts_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await ClockCycles(dut.sample_clk, 1)


def _pack(values: list[int], width: int) -> int:
    out = 0
    for k, v in enumerate(values):
        out |= (v & ((1 << width) - 1)) << (k * width)
    return out


async def _arm_seq(dut, values: list[int], masks: list[int], counts: list[int]):
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.seq_enable_in.value = 1
    dut.seq_values_in.value = _pack(values, G_SAMPLE_W)
    dut.seq_masks_in.value = _pack(masks, G_SAMPLE_W)
    dut.seq_counts_in.value = _pack(counts, 16)
    await ClockCycles(dut.sample_clk, 5)
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0


async def _drive(dut, value: int):
    dut.probe_in.value = value
    await RisingEdge(dut.sample_clk)


# 64-bit per-stage values, non-trivial bits in BOTH words.
V0 = 0xAAAA_BBBB_CCCC_DDDD
V1 = 0x1111_2222_3333_4444


@cocotb.test()
@requires("REA-REQ-013")
async def test_seq_wide_full_sequence_fires(dut):
    """A 2-stage sequence keyed on 64-bit per-stage values fires when
    both stages match in order on the full width."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm_seq(dut, [V0, V1], [FULL_MASK, FULL_MASK], [1, 1])

    await _drive(dut, V0)               # stage 0 (non-final) advances
    await ClockCycles(dut.sample_clk, PIPE_STAGES + 1)
    assert int(dut.triggered.value) == 0, "non-final stage must not fire"

    await _drive(dut, V1)               # stage 1 (final) fires
    await ClockCycles(dut.sample_clk, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, (
        "RTL-P3.691: full 64-bit 2-stage sequence did not fire"
    )
    dut._log.info("P3.691 PASS — wide multi-stage sequence fires on full width")


@cocotb.test()
@requires("REA-REQ-013")
async def test_seq_wide_stage_upper_word_participates(dut):
    """ADVERSARIAL: at the FINAL stage, a probe matching the low 32 bits
    of the stage value but differing in the upper word must NOT match —
    proving the per-stage comparator is full-width, not 32-bit-capped."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm_seq(dut, [V0, V1], [FULL_MASK, FULL_MASK], [1, 1])

    # Advance past stage 0 with an exact full-width match.
    await _drive(dut, V0)
    await ClockCycles(dut.sample_clk, PIPE_STAGES + 1)
    assert int(dut.triggered.value) == 0

    # Now at stage 1 (final). Drive a near-miss: low 32 of V1 match,
    # upper word zeroed → must NOT fire.
    near_miss = V1 & 0x0000_0000_FFFF_FFFF
    assert near_miss != V1
    for _ in range(PIPE_STAGES + 1):
        await _drive(dut, near_miss)
        assert int(dut.triggered.value) == 0, (
            "P3.691 FAIL: final stage fired on a low-32-match / upper-word-"
            "mismatch — the per-stage comparator is NOT full-width"
        )

    # Exact full-width V1 → fires, proving the FSM was armed and correct.
    await _drive(dut, V1)
    await ClockCycles(dut.sample_clk, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, (
        "control: exact full-width final match should fire"
    )
    dut._log.info("P3.691 PASS — per-stage comparator upper word participates")


if __name__ == "__main__":
    main()
