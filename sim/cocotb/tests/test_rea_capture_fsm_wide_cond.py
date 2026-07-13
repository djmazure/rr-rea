# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — comparator-array condition addressing bits ABOVE 255.
#
# RTL-P2.876: a per-condition comparator field can now sit above bit 255 (the
# regbank expands the 11-bit COND_CFG field_lsb into a full-width shifted mask/
# value; here we drive that full-width mask/value directly into the FSM). This
# proves the width-slice comparator pipeline fires on a masked-field match at a
# HIGH bit offset (slice index 64 = bit 512) at the field's 704-bit width —
# the on-silicon path a core without the extended lsb could never reach.
#
# Elaborated at G_SAMPLE_W=704, G_TRIG_CONDS=1 (minimal comparator array →
# C_REDUCE_STAGES=0, keeps the 88-stage width pipe tractable).
#
# Run via: rr sim run --ip <rea-dir> test_rea_capture_fsm_wide_cond
#     or:  PYTHONPATH=sim/cocotb:. python test_rea_capture_fsm_wide_cond.py

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

G_SAMPLE_W = 704
G_TRIG_CONDS = 1
GENERICS = {"G_SAMPLE_W": G_SAMPLE_W, "G_DEPTH": 1024, "G_TRIG_CONDS": G_TRIG_CONDS}
_RTL_DIR = str(_Path(__file__).resolve().parents[4] / "ip" / "routertl" / "rea" / "rtl")

OP_EQ = 0
# Width-slice pipe depth: ceil(W/8) + clog2(G_TRIG_CONDS). G_TRIG_CONDS=1 → +0.
PIPE_STAGES = (G_SAMPLE_W + 7) // 8 + (G_TRIG_CONDS - 1).bit_length()

# Field under test: [515:512], EQ 0xA. Mask/value are full 704-bit, shifted to
# bit 512 — exactly what the regbank produces from a COND_CFG with lsb_hi set.
FIELD_LSB = 512
FIELD_MASK = 0xF << FIELD_LSB
FIELD_VALUE = 0xA << FIELD_LSB


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_wide_cond",
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
    dut.trig_mode_in.value = 0
    dut.array_enable_in.value = 0
    dut.cond_values_in.value = 0
    dut.cond_masks_in.value = 0
    dut.cond_ops_in.value = 0
    dut.cond_valid_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await ClockCycles(dut.sample_clk, 1)


async def _arm(dut, pretrig: int = 4, posttrig: int = 4):
    dut.pretrig_len_in.value = pretrig
    dut.posttrig_len_in.value = posttrig
    dut.array_enable_in.value = 1
    dut.cond_values_in.value = FIELD_VALUE
    dut.cond_masks_in.value = FIELD_MASK
    dut.cond_ops_in.value = OP_EQ
    dut.cond_valid_in.value = 1
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0


@cocotb.test()
@requires("REA-REQ-017")
async def test_high_offset_condition_fires(dut):
    """A probe whose [515:512] field == 0xA fires; other bits are don't-care
    (masked out). Proves the comparator reaches bit offset 512 at 704 width."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm(dut)

    # Match in the high field, arbitrary noise elsewhere (masked away).
    probe = FIELD_VALUE | 0x1234_5678 | (0x1 << 700)
    dut.probe_in.value = probe
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0
    await ClockCycles(dut.sample_clk, PIPE_STAGES + 2)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, (
        "RTL-P2.876: high-offset (bit 512) masked-field match did not fire"
    )
    dut._log.info("REA-REQ-017 PASS — comparator fires on a match at bit 512")


@cocotb.test()
@requires("REA-REQ-017")
async def test_high_offset_condition_wrong_value_no_fire(dut):
    """ADVERSARIAL: the SAME low bits but the wrong value in [515:512] must NOT
    fire — the high field really is being compared (not ignored/aliased low)."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm(dut)

    # [515:512] = 0x5 (!= 0xA), plus the exact target value sitting at bit 0
    # (the wrong-bits trap: a low-8-bit-lsb decode would match this).
    wrong = (0x5 << FIELD_LSB) | 0xA
    dut.probe_in.value = wrong
    for _ in range(PIPE_STAGES + 2):
        await RisingEdge(dut.sample_clk)
        assert int(dut.triggered.value) == 0, (
            "RTL-P2.876 FAIL: fired on wrong high-field value / low-bit decoy — "
            "the comparator is looking at the wrong bits"
        )

    # Control: the correct high-field value still fires.
    dut.probe_in.value = FIELD_VALUE
    await RisingEdge(dut.sample_clk)
    await ClockCycles(dut.sample_clk, PIPE_STAGES + 2)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, "control: correct high-field match must fire"
    dut._log.info("REA-REQ-017 PASS — high-offset field genuinely compared")


if __name__ == "__main__":
    main()
