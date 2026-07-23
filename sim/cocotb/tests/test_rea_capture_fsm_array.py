# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — per-condition comparator-array (mixed-op AND) tests.
#
# RTL-P3.647: array_enable_in selects the AND-of-conditions path — each valid
# slot k applies its OWN op to its masked field; the trigger fires only when
# ALL valid slots match. Elaborated at G_TRIG_CONDS=4 with a "low nibble < 5
# AND high nibble == 1" mixed-op trigger (LT + EQ — the case the legacy single
# op nibble cannot express and the composer used to refuse).
#
# Run via:  rr sim run --ip <rea-dir> test_rea_capture_fsm_array

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

G_SAMPLE_W = 16
G_TRIG_CONDS = 4
DERIVED_PIPE_STAGES = 4
GENERICS = {"G_SAMPLE_W": G_SAMPLE_W, "G_DEPTH": 256, "G_TRIG_CONDS": G_TRIG_CONDS}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")

# Op codes — mirror rr_rea_pkg C_TRIG_OP_*.
OP_EQ, OP_NE, OP_LT, OP_GT, OP_RISE, OP_FALL = 0, 1, 2, 3, 4, 5


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_array",
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


def _pack(conds: list[tuple[int, int, int, bool]]):
    """conds: list of (value, mask, op, valid). Slot k → bit/field k.
    Returns (cond_values, cond_masks, cond_ops, cond_valid) ints."""
    cv = cm = co = cval = 0
    for k, (v, m, op, vld) in enumerate(conds):
        cv |= (v & ((1 << G_SAMPLE_W) - 1)) << (k * G_SAMPLE_W)
        cm |= (m & ((1 << G_SAMPLE_W) - 1)) << (k * G_SAMPLE_W)
        co |= (op & 0xF) << (k * 4)
        if vld:
            cval |= 1 << k
    return cv, cm, co, cval


async def _arm_array(dut, conds: list[tuple[int, int, int, bool]],
                     pretrig: int = 4, posttrig: int = 4):
    cv, cm, co, cval = _pack(conds)
    dut.pretrig_len_in.value = pretrig
    dut.posttrig_len_in.value = posttrig
    dut.array_enable_in.value = 1
    dut.cond_values_in.value = cv
    dut.cond_masks_in.value = cm
    dut.cond_ops_in.value = co
    dut.cond_valid_in.value = cval
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0


# "low nibble < 5 AND high nibble == 1": two valid slots, two invalid.
#   slot0: field [3:0],  op LT, value 5,   mask 0x000F
#   slot1: field [7:4],  op EQ, value 0x10, mask 0x00F0
_CONDS = [
    (0x0005, 0x000F, OP_LT, True),
    (0x0010, 0x00F0, OP_EQ, True),
    (0, 0, OP_EQ, False),
    (0, 0, OP_EQ, False),
]


async def _fire_on(dut, probe: int) -> bool:
    """Drive one probe sample and allow its derived pipeline to report."""
    dut.probe_in.value = probe
    await RisingEdge(dut.sample_clk)
    dut.probe_in.value = 0
    await ClockCycles(dut.sample_clk, DERIVED_PIPE_STAGES + 1)
    return int(dut.triggered.value) == 1


@cocotb.test()
@requires("REA-REQ-608")
async def test_array_and_both_conditions_fire(dut):
    """Both conditions satisfied → fire. 0x13: low=3 (<5 ✓), high=1 (==1 ✓)."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm_array(dut, _CONDS)
    assert await _fire_on(dut, 0x13), (
        "RTL-P3.647: both conditions met (low<5 AND high==1) must fire"
    )
    dut._log.info("P3.647 PASS — mixed-op AND fires when both conditions match")


@cocotb.test()
@requires("REA-REQ-608")
async def test_array_one_condition_fails_no_fire(dut):
    """ADVERSARIAL: this is an AND, not an OR. If EITHER condition fails the
    trigger must NOT fire — the whole point vs the legacy single comparator."""
    await _start_clk(dut)
    await _reset(dut)
    await _arm_array(dut, _CONDS)
    # low nibble 7 is NOT < 5 (cond0 fails), high nibble 1 (cond1 ok).
    for _ in range(4):
        dut.probe_in.value = 0x17
        await RisingEdge(dut.sample_clk)
        assert int(dut.triggered.value) == 0, "cond0 (low<5) failed — must not fire"

    # re-arm; high nibble 2 != 1 (cond1 fails), low nibble 3 (<5 ok).
    await _arm_array(dut, _CONDS)
    for _ in range(4):
        dut.probe_in.value = 0x23
        await RisingEdge(dut.sample_clk)
        assert int(dut.triggered.value) == 0, "cond1 (high==1) failed — must not fire"

    # control: 0x13 satisfies both → fires.
    assert await _fire_on(dut, 0x13)
    dut._log.info("P3.647 PASS — AND semantics: one failing condition blocks")


@cocotb.test()
@requires("REA-REQ-608")
async def test_array_invalid_slots_do_not_block(dut):
    """Invalid slots (valid=0) must not gate the trigger — only the two valid
    conditions decide. Slots 2,3 are invalid with junk masks; still fires."""
    await _start_clk(dut)
    await _reset(dut)
    conds = [
        (0x0005, 0x000F, OP_LT, True),
        (0x0010, 0x00F0, OP_EQ, True),
        (0xFFFF, 0xFFFF, OP_EQ, False),   # junk, but invalid → ignored
        (0x0002, 0x0F00, OP_GT, False),   # junk, but invalid → ignored
    ]
    await _arm_array(dut, conds)
    assert await _fire_on(dut, 0x13), "invalid slots must not block a valid match"
    dut._log.info("P3.647 PASS — invalid slots are ignored by the AND")


@cocotb.test()
@requires("REA-REQ-608")
async def test_array_all_invalid_never_fires(dut):
    """An all-invalid array must NOT free-fire on arm (the any-valid gate)."""
    await _start_clk(dut)
    await _reset(dut)
    conds = [(0, 0, OP_EQ, False)] * 4
    await _arm_array(dut, conds)
    for _ in range(8):
        dut.probe_in.value = 0x13
        await RisingEdge(dut.sample_clk)
        assert int(dut.triggered.value) == 0, "no valid condition → must not fire"
    dut._log.info("P3.647 PASS — all-invalid array does not free-fire")


@cocotb.test()
@requires("REA-REQ-600")
async def test_array_disabled_is_legacy_path(dut):
    """array_enable=0 → the legacy single-comparator path is unchanged
    (back-compat). Drive a plain EQ trigger via trig_value/mask."""
    await _start_clk(dut)
    await _reset(dut)
    # legacy EQ on full word == 0x00AA, array OFF.
    dut.pretrig_len_in.value = 4
    dut.posttrig_len_in.value = 4
    dut.trig_value_in.value = 0x00AA
    dut.trig_mask_in.value = 0x00FF
    dut.trig_mode_in.value = 0x01  # value_match | EQ
    dut.array_enable_in.value = 0
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0
    assert await _fire_on(dut, 0x00AA), "legacy EQ path must still fire when array off"
    dut._log.info("P3.647 PASS — array disabled leaves the legacy path intact")


if __name__ == "__main__":
    main()
