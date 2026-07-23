# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — wide-probe trigger comparator tests (RTL-P2.658).
#
# Elaborated at G_SAMPLE_W=64 to prove the trigger comparator fires on
# the FULL probe width, not just the low 32 bits. The adversarial case
# (REA-REQ-013) is the one that matters: a probe matching the low 32
# bits but differing in the UPPER word must NOT trigger — exactly the
# false match the legacy 32-bit-capped value/mask would have produced.
#
# Run via:  rr sim run --ip <rea-dir> test_rea_capture_fsm_wide

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
G_TRIG_CONDS = 4
GENERICS = {
    "G_SAMPLE_W": G_SAMPLE_W,
    "G_DEPTH": 256,
    "G_TRIG_CONDS": G_TRIG_CONDS,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")

FULL_MASK = (1 << G_SAMPLE_W) - 1
PIPE_STAGES = (G_SAMPLE_W + 7) // 8 + (G_TRIG_CONDS - 1).bit_length()


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_wide",
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
    cocotb.start_soon(Clock(dut.sample_clk_i, CLK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.arm_pulse_i.value = 0
    dut.reset_pulse_i.value = 0
    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 0
    dut.trig_value_i.value = 0
    dut.trig_mask_i.value = 0
    dut.trig_mode_i.value = 0
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 1)


async def _arm(dut, value: int, mask: int, pretrig: int = 4, posttrig: int = 4):
    """Latch the wide value/mask and pulse arm (config captured on arm)."""
    dut.pretrig_len_i.value = pretrig
    dut.posttrig_len_i.value = posttrig
    dut.trig_value_i.value = value
    dut.trig_mask_i.value = mask
    dut.trig_mode_i.value = 0  # EQ
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0


# 64-bit pattern with non-trivial bits in BOTH words.
VALUE = 0xDEAD_BEEF_0123_4567


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_wide_exact_match_fires(dut):
    """A 64-bit probe equal to the full trig_value fires the trigger."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm(dut, VALUE, FULL_MASK)

    # Hold a non-matching probe — triggered must stay 0.
    dut.probe_i.value = VALUE ^ 0x1  # one bit off
    for _ in range(3):
        await RisingEdge(dut.sample_clk_i)
        assert int(dut.triggered_o.value) == 0, "fired on a non-match"

    # Drive the exact 64-bit match through the derived slice pipeline.
    dut.probe_i.value = VALUE
    await RisingEdge(dut.sample_clk_i)
    await ClockCycles(dut.sample_clk_i, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered_o.value) == 1, (
        "REA-REQ-013: exact 64-bit match did not fire the trigger"
    )
    dut._log.info("REA-REQ-013 PASS — wide exact match fires")


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_upper_word_participates(dut):
    """ADVERSARIAL: a probe matching the LOW 32 bits but differing in the
    UPPER word must NOT trigger. This is the exact false-match the legacy
    32-bit-capped value/mask would have produced — the regression this
    whole ticket exists to kill."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm(dut, VALUE, FULL_MASK)

    # Low 32 bits match, upper word zeroed → differs above bit 31.
    near_miss = VALUE & 0x0000_0000_FFFF_FFFF
    assert near_miss != VALUE, "test setup: near_miss must differ"
    dut.probe_i.value = near_miss
    for _ in range(PIPE_STAGES + 1):
        await RisingEdge(dut.sample_clk_i)
        assert int(dut.triggered_o.value) == 0, (
            "REA-REQ-013 FAIL: triggered on a low-32-match / upper-word-"
            "mismatch — the upper word is NOT being compared (the legacy bug)"
        )

    # Sanity: the FSM IS armed and working — the exact value still fires.
    dut.probe_i.value = VALUE
    await RisingEdge(dut.sample_clk_i)
    await ClockCycles(dut.sample_clk_i, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered_o.value) == 1, (
        "control: exact match should still fire after the near-miss"
    )
    dut._log.info("REA-REQ-013 PASS — upper word participates in the compare")


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_wide_masked_match(dut):
    """A partial mask covering bits in BOTH words: only the masked field
    must match. Don't-care bits (mask=0) outside the field are ignored."""
    await _start_clk(dut)
    await _reset(dut)

    # Mask the top byte of the upper word + the low byte of word 0.
    mask = 0xFF00_0000_0000_00FF
    value = 0xAB00_0000_0000_00CD
    await _arm(dut, value, mask)

    # Probe whose masked field equals value's masked field, but whose
    # don't-care bits are arbitrary garbage → must still fire.
    probe = 0xABFF_FFFF_FFFF_FFCD
    dut.probe_i.value = probe
    await RisingEdge(dut.sample_clk_i)
    await ClockCycles(dut.sample_clk_i, PIPE_STAGES)
    await ReadOnly()
    assert int(dut.triggered_o.value) == 1, (
        "REA-REQ-013: masked field matched but trigger did not fire"
    )
    dut._log.info("REA-REQ-013 PASS — wide masked match fires on masked field")


if __name__ == "__main__":
    main()
