# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.7 — the multi-stage trigger sequencer through rr_rea_top's JTAG door
(REA-REQ-601..607).

Before REA-P3.7 the sequencer lived only in rr_rea_capture_fsm: rr_rea_top
never passed G_TRIG_STAGES, the SEQ window (0x40..0x9F) decoded nothing and
TRIG_MODE bit[1] reached no enable, so no host could arm it. These tests drive
the whole path a host uses — SEQ registers over JTAG, paged by TRIG_WORD_SEL,
across the CDC into the FSM — on a 3-stage core with a 40-bit probe. The
stage patterns differ ONLY in the second 32-bit page, so the ordering tests
can pass only if that page of every stage's value and mask reached the FSM.

The oracle is the testbench's own record of what it put on probe_i and when;
the trigger cell read back through DATA_BASE must be the final stage's
pattern, and trigger_o must stay low until that pattern arrives in order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_SIM_ROOT = str(Path(__file__).resolve().parents[2])
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)

from test_rea_top import (  # noqa: E402
    _jtag_read,
    _jtag_write,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

SAMPLE_W = 40
STAGES = 3
GENERICS = {
    "G_SAMPLE_W": SAMPLE_W,
    "G_DEPTH": 64,
    "G_TIMESTAMP_W": 0,
    "G_NUM_CHAN": 1,
    "G_TRIG_STAGES": STAGES,
}
_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_VERSION = 0x00
ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_TRIG_WORD_SEL = 0x2C
ADDR_SEQ_BASE = 0x40
SEQ_STRIDE = 20
OFF_CFG, OFF_VALUE, OFF_MASK, OFF_VALUE_B, OFF_MASK_B = 0, 4, 8, 12, 16
ADDR_CHAN_SEL = 0xA0
ADDR_START_PTR = 0xC8
ADDR_DATA_WORD_SEL = 0xCC
ADDR_FEATURES = 0xD0
ADDR_DATA_BASE = 0x100

CTRL_ARM = 0x1
STATUS_ARMED, STATUS_TRIGGERED, STATUS_DONE = 0x1, 0x2, 0x4
TRIG_MODE_SEQ = 1 << 1          # C_TRIG_MODE_BIT_SEQ_EN
VERSION_P3_7 = 0x5245410F       # v0.15 tier (tier byte stays odd, REQ-806)
FULL_MASK = (1 << SAMPLE_W) - 1
PRETRIG, POSTTRIG = 8, 8

# Stage patterns: IDENTICAL in the low 32-bit page and distinct only in the
# high page, never zero (the filler). If the high page of a stage's value or
# mask did not reach the FSM, the three stages would be indistinguishable and
# the out-of-order sequence below would fire (the REA-P3.7 mutation check).
PAT_A = 0x81_0000_00AA
PAT_B = 0x42_0000_00AA
PAT_C = 0x24_0000_00AA
FILLER = 0


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module=Path(__file__).stem,
        custom_libraries={
            "work": [
                str(_RTL_DIR / "rr_rea_pkg.vhd"),
                str(_FIX / "rr_rea_build_id_stub.vhd"),
                str(_RTL_DIR / "rr_rea_dpram.vhd"),
                str(_RTL_DIR / "rr_rea_capture_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_regbank.vhd"),
                str(_RTL_DIR / "rr_rea_cdc.vhd"),
                str(_RTL_DIR / "rr_rea_jtag_iface.vhd"),
                str(_RTL_DIR / "rr_rea_crc_sweep.vhd"),
                str(_RTL_DIR / "rr_rea_fill_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_trust_core.vhd"),
                str(_RTL_DIR / "rr_rea_axis_window.vhd"),
                str(_RTL_DIR / "rr_rea_top.vhd"),
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


def _seq(stage: int, off: int) -> int:
    return ADDR_SEQ_BASE + stage * SEQ_STRIDE + off


async def _write_paged(dut, addr: int, value: int) -> None:
    """Write a SAMPLE_W-bit field through its 32-bit window, one page per
    TRIG_WORD_SEL value, then restore the selector to 0 (the host contract)."""
    for page in range((SAMPLE_W + 31) // 32):
        await _jtag_write(dut, ADDR_TRIG_WORD_SEL, page)
        await _jtag_write(dut, addr, (value >> (32 * page)) & 0xFFFF_FFFF)
    await _jtag_write(dut, ADDR_TRIG_WORD_SEL, 0)


async def _program_stages(dut, stages: list) -> None:
    for k, (value, mask, count) in enumerate(stages):
        await _jtag_write(dut, _seq(k, OFF_CFG), count)
        await _write_paged(dut, _seq(k, OFF_VALUE), value)
        await _write_paged(dut, _seq(k, OFF_MASK), mask)


class Probe:
    """Holds probe_i at FILLER except for scripted one-cycle events, and
    records every sample-clock cycle on which trigger_o is high."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.trigger_cycles: list = []
        dut.probe_i.value = FILLER

    async def watch(self) -> None:
        while True:
            await RisingEdge(self.dut.sample_clk_i)
            self.cycle += 1
            await ReadOnly()
            if self.dut.trigger_o.value.is_resolvable and int(self.dut.trigger_o.value):
                self.trigger_cycles.append(self.cycle)

    async def play(self, patterns: list, gap: int = 12) -> list:
        """Put each pattern on probe_i for exactly one sample cycle, `gap`
        filler cycles apart. Returns the cycle each pattern was presented."""
        at = []
        for pat in patterns:
            await ClockCycles(self.dut.sample_clk_i, gap)
            self.dut.probe_i.value = pat
            at.append(self.cycle + 1)
            await RisingEdge(self.dut.sample_clk_i)
            self.dut.probe_i.value = FILLER
        await ClockCycles(self.dut.sample_clk_i, gap)
        return at


async def _arm(dut) -> None:
    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    for _ in range(100):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_ARMED:
            return
    raise AssertionError("core never reported ARMED after the arm write")


async def _setup(dut, stages: list) -> Probe:
    await _start_clocks(dut)
    await _reset(dut)
    probe = Probe(dut)
    cocotb.start_soon(probe.watch())
    await ClockCycles(dut.sample_clk_i, 2 * GENERICS["G_DEPTH"])
    await _jtag_write(dut, ADDR_PRETRIG, PRETRIG)
    await _jtag_write(dut, ADDR_POSTTRIG, POSTTRIG)
    await _program_stages(dut, stages)
    await _jtag_write(dut, ADDR_TRIG_MODE, TRIG_MODE_SEQ)
    await _arm(dut)
    # Let the pre-trigger window fill with filler before any event.
    await ClockCycles(dut.sample_clk_i, 4 * PRETRIG)
    return probe


async def _wait_done(dut) -> None:
    for _ in range(200):
        if await _jtag_read(dut, ADDR_STATUS) & STATUS_DONE:
            return
        await ClockCycles(dut.tck_i, 4)
    raise AssertionError("capture never completed after the final stage")


async def _trigger_cell(dut) -> int:
    depth = GENERICS["G_DEPTH"]
    start = await _jtag_read(dut, ADDR_START_PTR) & (depth - 1)
    addr = ADDR_DATA_BASE + 4 * ((start + PRETRIG) % depth)
    value = 0
    for page in range((SAMPLE_W + 31) // 32):
        await _jtag_write(dut, ADDR_DATA_WORD_SEL, page)
        value |= (await _jtag_read(dut, addr)) << (32 * page)
    await _jtag_write(dut, ADDR_DATA_WORD_SEL, 0)
    return value & FULL_MASK


async def _assert_not_triggered(dut, probe: Probe, why: str) -> None:
    assert not probe.trigger_cycles, (
        f"{why}: trigger_o fired at sample cycle(s) {probe.trigger_cycles[:4]}")
    status = await _jtag_read(dut, ADDR_STATUS)
    assert not status & STATUS_TRIGGERED, f"{why}: STATUS=0x{status:02X} TRIGGERED"


@cocotb.test()
@requires("REA-REQ-607", "REA-REQ-967")
async def test_rea_req_607_seq_window_decodes_through_the_top(dut):
    """VERSION is the v0.15 tier and FEATURES[30:28] reports G_TRIG_STAGES.
    Each stage's cfg (count_target [15:0] only), value and mask round-trip,
    value/mask on BOTH pages of TRIG_WORD_SEL. Reserved offsets, a stage past
    G_TRIG_STAGES and cfg bits above [15:0] drop their writes and read 0, and
    nothing aliases into the v0.1 registers."""
    await _start_clocks(dut)
    await _reset(dut)

    assert await _jtag_read(dut, ADDR_VERSION) == VERSION_P3_7
    features = await _jtag_read(dut, ADDR_FEATURES)
    assert (features >> 28) & 0x7 == STAGES, (
        f"FEATURES=0x{features:08X}: [30:28] should carry G_TRIG_STAGES={STAGES}")
    assert (features >> 31) & 1 == 0 and (features >> 23) & 1 == 0, (
        f"FEATURES=0x{features:08X}: reserved bit set")

    await _jtag_write(dut, ADDR_PRETRIG, 0x0000_0007)
    await _jtag_write(dut, ADDR_CHAN_SEL, 0x0000_0000)
    want = {k: (0x1111 * (k + 1),
                (0xA5 << 32) | (0x1000_0000 + k),
                (0x5A << 32) | (0x0F0F_0000 + k)) for k in range(STAGES)}
    for k, (count, value, mask) in want.items():
        await _jtag_write(dut, _seq(k, OFF_CFG), 0xFFFF_0000 | count)
        await _write_paged(dut, _seq(k, OFF_VALUE), value)
        await _write_paged(dut, _seq(k, OFF_MASK), mask)
    for k, (count, value, mask) in want.items():
        assert await _jtag_read(dut, _seq(k, OFF_CFG)) == count, (
            f"stage {k} SEQ_CFG must keep only count_target [15:0]")
        for page in range(2):
            await _jtag_write(dut, ADDR_TRIG_WORD_SEL, page)
            got_v = await _jtag_read(dut, _seq(k, OFF_VALUE))
            got_m = await _jtag_read(dut, _seq(k, OFF_MASK))
            assert got_v == (value >> (32 * page)) & 0xFFFF_FFFF, (
                f"stage {k} SEQ_VALUE page {page} = 0x{got_v:08X}")
            assert got_m == (mask >> (32 * page)) & 0xFFFF_FFFF, (
                f"stage {k} SEQ_MASK page {page} = 0x{got_m:08X}")
        await _jtag_write(dut, ADDR_TRIG_WORD_SEL, 0)

    # Reserved (+0x0C, +0x10) and a stage beyond the build: dropped, read 0.
    dead = [_seq(k, off) for k in range(STAGES) for off in (OFF_VALUE_B, OFF_MASK_B)]
    dead += [_seq(STAGES, off) for off in (OFF_CFG, OFF_VALUE, OFF_MASK)]
    for addr in dead:
        await _jtag_write(dut, addr, 0xFFFF_FFFF)
    for addr in dead:
        got = await _jtag_read(dut, addr)
        assert got == 0, f"reserved/unbuilt SEQ address 0x{addr:02X} read 0x{got:08X}"
    # And the live stages were not disturbed by those writes.
    assert await _jtag_read(dut, _seq(STAGES - 1, OFF_CFG)) == want[STAGES - 1][0]
    assert await _jtag_read(dut, ADDR_PRETRIG) == 0x0000_0007, "SEQ write aliased PRETRIG"


@cocotb.test()
@requires("REA-REQ-601", "REA-REQ-602", "REA-REQ-604", "REA-REQ-605")
async def test_rea_req_601_605_in_order_traversal_is_the_only_trigger(dut):
    """Armed with A -> B -> C: the later-stage patterns shown out of order (C,
    B, then C again after A) never fire, and neither does a non-final stage's
    own match. Only C after A then B fires, and the trigger cell captured is
    C — all 40 bits, both pages."""
    probe = await _setup(dut, [(PAT_A, FULL_MASK, 0), (PAT_B, FULL_MASK, 0),
                               (PAT_C, FULL_MASK, 0)])

    await probe.play([PAT_C, PAT_B])            # later stages first (REQ-605)
    await _assert_not_triggered(dut, probe, "C then B before A")
    await probe.play([PAT_A])                   # stage 0 advances only (REQ-604)
    await _assert_not_triggered(dut, probe, "A alone")
    await probe.play([PAT_C])                   # stage 2 while on stage 1
    await _assert_not_triggered(dut, probe, "C while waiting for B")
    await probe.play([PAT_B])                   # stage 1 advances (REQ-601)
    await _assert_not_triggered(dut, probe, "A then B")
    at = await probe.play([PAT_C])              # final stage fires (REQ-602)

    assert probe.trigger_cycles, "in-order A, B, C never fired trigger_o"
    assert probe.trigger_cycles[0] >= at[0], (
        f"trigger_o at cycle {probe.trigger_cycles[0]}, before C at {at[0]}")
    await _wait_done(dut)
    cell = await _trigger_cell(dut)
    assert cell == PAT_C, f"trigger cell 0x{cell:010X}, want C 0x{PAT_C:010X}"


@cocotb.test()
@requires("REA-REQ-603")
async def test_rea_req_603_count_target_needs_that_many_matches(dut):
    """Stage 1 with count_target 3 needs three B matches before C can fire:
    A, B, B, C must not fire; one more B then C does."""
    probe = await _setup(dut, [(PAT_A, FULL_MASK, 0), (PAT_B, FULL_MASK, 3),
                               (PAT_C, FULL_MASK, 0)])
    await probe.play([PAT_A, PAT_B, PAT_B, PAT_C])
    await _assert_not_triggered(dut, probe, "only two of three B matches")
    await probe.play([PAT_B, PAT_C])
    assert probe.trigger_cycles, "third B then C never fired"
    await _wait_done(dut)
    assert await _trigger_cell(dut) == PAT_C


@cocotb.test()
@requires("REA-REQ-606")
async def test_rea_req_606_rearm_restarts_the_sequence(dut):
    """A re-arm mid-sequence (after A, B) resets to stage 0: C alone then does
    not fire, and a full A, B, C does."""
    probe = await _setup(dut, [(PAT_A, FULL_MASK, 0), (PAT_B, FULL_MASK, 0),
                               (PAT_C, FULL_MASK, 0)])
    await probe.play([PAT_A, PAT_B])
    await _arm(dut)
    await ClockCycles(dut.sample_clk_i, 4 * PRETRIG)
    await probe.play([PAT_C])
    await _assert_not_triggered(dut, probe, "C right after a re-arm")
    await probe.play([PAT_A, PAT_B, PAT_C])
    assert probe.trigger_cycles, "full sequence after re-arm never fired"
    await _wait_done(dut)
    assert await _trigger_cell(dut) == PAT_C


@cocotb.test()
@requires("REA-REQ-600")
async def test_rea_req_600_seq_disabled_keeps_the_single_comparator(dut):
    """With TRIG_MODE bit[1] clear the programmed stages are ignored: the
    single TRIG_VALUE comparator fires on its own pattern and A, B, C in order
    do nothing."""
    await _start_clocks(dut)
    await _reset(dut)
    probe = Probe(dut)
    cocotb.start_soon(probe.watch())
    await ClockCycles(dut.sample_clk_i, 2 * GENERICS["G_DEPTH"])
    await _jtag_write(dut, ADDR_PRETRIG, PRETRIG)
    await _jtag_write(dut, ADDR_POSTTRIG, POSTTRIG)
    await _program_stages(dut, [(PAT_A, FULL_MASK, 0), (PAT_B, FULL_MASK, 0),
                                (PAT_C, FULL_MASK, 0)])
    other = 0x11_0000_0077
    await _write_paged(dut, ADDR_TRIG_VALUE, other)
    await _write_paged(dut, ADDR_TRIG_MASK, FULL_MASK)
    await _jtag_write(dut, ADDR_TRIG_MODE, 0x1)       # value_match, seq off
    await _arm(dut)
    await ClockCycles(dut.sample_clk_i, 4 * PRETRIG)
    await probe.play([PAT_A, PAT_B, PAT_C])
    await _assert_not_triggered(dut, probe, "sequence with TRIG_MODE bit[1] clear")
    await probe.play([other])
    assert probe.trigger_cycles, "single comparator never fired"
    await _wait_done(dut)
    assert await _trigger_cell(dut) == other


if __name__ == "__main__":
    main()
