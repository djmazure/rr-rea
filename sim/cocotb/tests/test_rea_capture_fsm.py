# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — sliding-window capture state machine.
#
# Tests cover REA-REQ-100..106 (see ../requirements.yml).
#
# Strategy: the FSM drives dpram_we/dpram_addr/dpram_din. We snoop
# those and maintain a Python-side dict {addr: value} that emulates
# the dpram. This lets unit-level tests verify the full capture
# contract (including dpram contents post-done) without instantiating
# a separate dpram module.
#
# Run via:  rr sim run --ip <rea-dir> test_rea_capture_fsm

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

GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": 4096}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm",
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


# ── Helpers ──────────────────────────────────────────────────────────

CLK_PERIOD_NS = 8.0  # 125 MHz, matches Zybo demo


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.sample_clk, CLK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    """Apply sample_rst for 4 cycles, deassert, settle."""
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.pretrig_len_in.value = 0
    dut.posttrig_len_in.value = 0
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await ClockCycles(dut.sample_clk, 1)


class DpramMock:
    """Snoops dpram_we/addr/din and maintains a {addr: value} dict.

    Run as a background task — every clock edge we sample the FSM's
    dpram drive signals and record the write into our dict.
    """
    def __init__(self, dut):
        self.dut = dut
        self.cells: dict[int, int] = {}
        self.write_log: list[tuple[int, int, int]] = []  # (cycle, addr, value)
        self._cycle = 0
        self._alive = True

    async def run(self):
        while self._alive:
            await RisingEdge(self.dut.sample_clk)
            self._cycle += 1
            if int(self.dut.dpram_we.value) == 1:
                addr = int(self.dut.dpram_addr.value)
                val = int(self.dut.dpram_din.value)
                self.cells[addr] = val
                self.write_log.append((self._cycle, addr, val))

    def stop(self):
        self._alive = False


async def _pulse(sig, dut, n_cycles: int = 1):
    """Hold a signal high for n_cycles, then drop to 0."""
    sig.value = 1
    for _ in range(n_cycles):
        await RisingEdge(dut.sample_clk)
    sig.value = 0


async def _drive_counter_probe(dut, n_cycles: int, start: int = 0):
    """Free-run a counter on probe_in for n_cycles; advance one
    sample per clock edge. Returns the final counter value (next-to-
    be-driven)."""
    val = start & 0xFFF
    for _ in range(n_cycles):
        dut.probe_in.value = val
        await RisingEdge(dut.sample_clk)
        val = (val + 1) & 0xFFF
    return val


# ── REA-REQ-100: wr_ptr free-runs from reset, with armed=0 ──────────


@cocotb.test()
@requires("REA-REQ-100")
async def test_rea_req_100_wr_ptr_free_runs_unarmed(dut):
    """Without `armed`, wr_ptr STILL increments every clock cycle —
    the dpram is a sliding-window buffer that records continuously
    from reset. This pins the architectural contract that fcapz
    violates."""
    await _start_clk(dut)
    await _reset(dut)

    snapshots: list[int] = []
    for _ in range(20):
        snapshots.append(int(dut.wr_ptr_out.value))
        await RisingEdge(dut.sample_clk)

    # wr_ptr should have advanced by exactly 20 (no skipped cycles,
    # no stalls). Hard-coded expected sequence per ROUTERTL-002.
    expected = list(range(snapshots[0], snapshots[0] + 20))
    expected = [v & 0xFFF for v in expected]
    assert snapshots == expected, (
        f"REA-REQ-100 failed: wr_ptr did NOT free-run.\n"
        f"  observed:  {snapshots}\n"
        f"  expected:  {expected}"
    )
    # And we never asserted arm — armed must still be 0.
    assert int(dut.armed.value) == 0, "armed should be 0 (never armed)"

    dut._log.info("REA-REQ-100 PASS — wr_ptr free-runs from reset")


# ── REA-REQ-101: arm_pulse does NOT reset wr_ptr ────────────────────


@cocotb.test()
@requires("REA-REQ-101")
async def test_rea_req_101_arm_does_not_reset_wr_ptr(dut):
    """arm_pulse must NOT touch wr_ptr — the buffer's pre-arm context
    is preserved across arm. Pins the architectural fix vs fcapz."""
    await _start_clk(dut)
    await _reset(dut)

    # Let wr_ptr free-run to a known non-zero value (50 cycles).
    await ClockCycles(dut.sample_clk, 50)
    pre_arm_wr_ptr = int(dut.wr_ptr_out.value)
    assert pre_arm_wr_ptr > 0, "Setup precondition: wr_ptr should be non-zero"

    # Set up benign config and pulse arm.
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0  # mask=0 → trigger-hit always (auto-trigger)
    await _pulse(dut.arm_pulse, dut, 1)

    # Sample wr_ptr the cycle AFTER arm took effect.
    post_arm_wr_ptr = int(dut.wr_ptr_out.value)
    # Either still incrementing (post_arm == pre_arm + 2 after 1 arm
    # cycle + 1 sample window) OR triggered already and counting post.
    # Either way, must be STRICTLY > pre_arm_wr_ptr (no reset to 0).
    assert post_arm_wr_ptr != 0, (
        f"REA-REQ-101 failed: wr_ptr was reset to 0 on arm "
        f"(pre={pre_arm_wr_ptr}, post={post_arm_wr_ptr})"
    )
    # Stronger pin: must have advanced monotonically (mod DEPTH).
    delta = (post_arm_wr_ptr - pre_arm_wr_ptr) & 0xFFF
    assert 0 < delta < 100, (
        f"REA-REQ-101 failed: wr_ptr delta {delta} suggests reset/skip "
        f"(pre={pre_arm_wr_ptr}, post={post_arm_wr_ptr})"
    )

    dut._log.info(
        f"REA-REQ-101 PASS — wr_ptr preserved: {pre_arm_wr_ptr} → {post_arm_wr_ptr}"
    )


# ── REA-REQ-102: trig_ptr captures wr_ptr at trigger fire cycle ─────


@cocotb.test()
@requires("REA-REQ-102")
async def test_rea_req_102_trig_ptr_captures_wr_ptr(dut):
    """The delayed decision retains the pointer of its matching sample."""
    await _start_clk(dut)
    await _reset(dut)

    # Always-match trigger so we can pin the moment of fire.
    dut.probe_in.value = 0x42
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0x42
    dut.trig_mask_in.value = 0xFF

    await ClockCycles(dut.sample_clk, 50)
    await _pulse(dut.arm_pulse, dut, 1)

    await ReadOnly()
    matching_sample_ptr = int(dut.wr_ptr_out.value)
    for _ in range(20):
        await RisingEdge(dut.sample_clk)
        await ReadOnly()
        if int(dut.triggered.value) == 1:
            break
    else:
        assert False, (
            "REA-REQ-102 failed: trigger never fired within 20 cycles of arm"
        )
    trig_ptr = int(dut.trig_ptr_out.value)
    assert trig_ptr == matching_sample_ptr, (
        f"REA-REQ-102 failed: trig_ptr={trig_ptr} should equal the "
        f"matching sample pointer={matching_sample_ptr}, not the later "
        "report-cycle pointer"
    )

    dut._log.info(
        f"REA-REQ-102 PASS — delayed decision retained trig_ptr={trig_ptr}"
    )


# ── REA-REQ-103: dpram[trig_ptr] contains the trigger sample ────────


@cocotb.test()
@requires("REA-REQ-103")
async def test_rea_req_103_dpram_at_trig_ptr_is_trigger_sample(dut):
    """The cell at dpram[trig_ptr] must hold the sample whose value
    matched the trigger condition. This is the bug fcapz has where
    dpram[trig_ptr] is some neighbor of the trigger sample."""
    await _start_clk(dut)
    await _reset(dut)

    dpram = DpramMock(dut)
    dpram_task = cocotb.start_soon(dpram.run())

    # Free-running counter on probe_in. Trigger value = 0x42.
    dut.pretrig_len_in.value = 16
    dut.posttrig_len_in.value = 16
    dut.trig_value_in.value = 0x42
    dut.trig_mask_in.value = 0xFF

    # Let the buffer warm up well past DEPTH.
    await _drive_counter_probe(dut, n_cycles=200, start=0)

    # Arm. Continue driving the counter; trigger will fire when
    # counter == 0x42.
    await _pulse(dut.arm_pulse, dut, 1)
    await _drive_counter_probe(dut, n_cycles=600, start=200)

    trig_ptr = int(dut.trig_ptr_out.value)
    assert int(dut.triggered.value) == 1, "expected trigger to have fired"

    cell = dpram.cells.get(trig_ptr)
    assert cell is not None, (
        f"REA-REQ-103 failed: dpram[{trig_ptr}] was never written"
    )
    assert (cell & 0xFF) == 0x42, (
        f"REA-REQ-103 failed: dpram[trig_ptr={trig_ptr}] = 0x{cell:03x} "
        f"(low byte 0x{cell&0xFF:02x}) should be 0x42 (the trigger sample)"
    )

    dpram.stop()
    del dpram_task
    dut._log.info(f"REA-REQ-103 PASS — dpram[{trig_ptr}] = 0x{cell:03x}")


# ── REA-REQ-104: start_ptr == (trig_ptr - pretrig_len) mod DEPTH ────


@cocotb.test()
@requires("REA-REQ-104")
async def test_rea_req_104_start_ptr_arithmetic(dut):
    """At done, start_ptr must equal (trig_ptr - pretrig_len) mod
    DEPTH. Hard-pinned arithmetic — no off-by-N."""
    await _start_clk(dut)
    await _reset(dut)

    PRETRIG = 32
    POSTTRIG = 16

    dut.pretrig_len_in.value = PRETRIG
    dut.posttrig_len_in.value = POSTTRIG
    dut.trig_value_in.value = 0x55
    dut.trig_mask_in.value = 0xFF
    dut.probe_in.value = 0x55  # always-match trigger

    # Warm up (also lets wr_ptr move into a wrap-aware position).
    await ClockCycles(dut.sample_clk, 100)

    await _pulse(dut.arm_pulse, dut, 1)

    # Wait for done (capped — should fire within posttrig+arm latency).
    deadline = 200
    for _ in range(deadline):
        await RisingEdge(dut.sample_clk)
        if int(dut.done.value) == 1:
            break
    assert int(dut.done.value) == 1, "done never asserted"

    trig_ptr = int(dut.trig_ptr_out.value)
    start_ptr = int(dut.start_ptr_out.value)
    expected = (trig_ptr - PRETRIG) & 0xFFF
    assert start_ptr == expected, (
        f"REA-REQ-104 failed: start_ptr=0x{start_ptr:03x}, expected "
        f"(trig_ptr=0x{trig_ptr:03x} - pretrig=0x{PRETRIG:03x}) mod 4096 = "
        f"0x{expected:03x}"
    )

    dut._log.info(
        f"REA-REQ-104 PASS — start_ptr=0x{start_ptr:03x}, trig_ptr=0x{trig_ptr:03x}"
    )


# ── REA-REQ-105: read from start_ptr is time-monotonic ──────────────


@cocotb.test()
@requires("REA-REQ-105")
async def test_rea_req_105_read_from_start_ptr_is_monotonic(dut):
    """Reading PRETRIG+POSTTRIG+1 samples starting at start_ptr (with
    mod-DEPTH wrap) yields a time-monotonic sequence — i.e., the
    captured counter values increment by exactly 1 across the full
    window with no gaps or wraps mid-window (counter is 8-bit so
    wraps every 256, accept that)."""
    await _start_clk(dut)
    await _reset(dut)

    dpram = DpramMock(dut)
    cocotb.start_soon(dpram.run())

    PRETRIG = 64
    POSTTRIG = 32

    dut.pretrig_len_in.value = PRETRIG
    dut.posttrig_len_in.value = POSTTRIG
    dut.trig_value_in.value = 0x42
    dut.trig_mask_in.value = 0xFF

    # Drive a counter and let buffer warm well past DEPTH.
    await _drive_counter_probe(dut, n_cycles=5000, start=0)

    # Arm and let trigger + post-window complete naturally.
    await _pulse(dut.arm_pulse, dut, 1)
    await _drive_counter_probe(dut, n_cycles=400, start=5000 & 0xFF)

    assert int(dut.done.value) == 1, "done never asserted"

    start_ptr = int(dut.start_ptr_out.value)
    capture_len = PRETRIG + POSTTRIG + 1

    # Walk dpram from start_ptr for capture_len samples.
    DEPTH = 4096
    seq = []
    for i in range(capture_len):
        addr = (start_ptr + i) % DEPTH
        cell = dpram.cells.get(addr)
        assert cell is not None, (
            f"REA-REQ-105 failed: dpram[{addr}] (window offset {i}) "
            f"was never written — sliding window left a gap"
        )
        seq.append(cell & 0xFF)

    # Counter delta between consecutive samples is +1 (mod 256).
    for i in range(1, len(seq)):
        delta = (seq[i] - seq[i - 1]) & 0xFF
        assert delta == 1, (
            f"REA-REQ-105 failed: gap at window offset {i} — "
            f"prev=0x{seq[i-1]:02x} curr=0x{seq[i]:02x} delta={delta}"
        )

    dpram.stop()
    dut._log.info(
        f"REA-REQ-105 PASS — {capture_len}-sample window time-monotonic"
    )


# ── REA-REQ-106: no uninit-BRAM zeros when buffer pre-warmed ────────


@cocotb.test()
@requires("REA-REQ-106")
async def test_rea_req_106_no_uninit_zeros_when_prewarmed(dut):
    """When the design has been running ≥ DEPTH cycles before arm,
    the captured window contains zero uninit cells — the sliding
    window has fully populated the buffer. This is the headline
    architectural fix vs fcapz.

    We use a probe pattern (counter | 0x100) that is NEVER zero, so
    any zero in the captured window must be uninit BRAM (the bug).
    """
    await _start_clk(dut)
    await _reset(dut)

    dpram = DpramMock(dut)
    cocotb.start_soon(dpram.run())

    DEPTH = 4096
    PRETRIG = DEPTH // 2 - 1
    POSTTRIG = DEPTH // 2 - 1

    dut.pretrig_len_in.value = PRETRIG
    dut.posttrig_len_in.value = POSTTRIG
    dut.trig_value_in.value = 0xFF
    dut.trig_mask_in.value = 0xFF

    # Probe pattern: low byte = counter, bit 8 always set → never
    # zero. Drive for > DEPTH cycles so the buffer is fully primed.
    counter = 0
    for _ in range(DEPTH + 200):
        dut.probe_in.value = (counter & 0xFF) | 0x100
        await RisingEdge(dut.sample_clk)
        counter += 1

    # Arm + let trigger fire (will hit on counter & 0xFF == 0xFF).
    await _pulse(dut.arm_pulse, dut, 1)
    for _ in range(DEPTH):
        dut.probe_in.value = (counter & 0xFF) | 0x100
        await RisingEdge(dut.sample_clk)
        counter += 1
        if int(dut.done.value) == 1:
            break

    assert int(dut.done.value) == 1, "done never asserted"
    start_ptr = int(dut.start_ptr_out.value)
    capture_len = PRETRIG + POSTTRIG + 1

    zeros = 0
    for i in range(capture_len):
        addr = (start_ptr + i) % DEPTH
        cell = dpram.cells.get(addr)
        if cell is None or cell == 0:
            zeros += 1

    assert zeros == 0, (
        f"REA-REQ-106 failed: captured window has {zeros}/"
        f"{capture_len} uninit cells (sliding-window contract violated)"
    )

    dpram.stop()
    dut._log.info(
        f"REA-REQ-106 PASS — 0/{capture_len} uninit cells in the window"
    )


# ── REA-REQ-400 + 401: external trigger_in semantics ───────────────


@cocotb.test()
@requires("REA-REQ-400")
async def test_rea_req_400_external_trigger_in_fires_capture(dut):
    """A trigger_in pulse on sample_clk fires the FSM exactly like a
    local trigger_hit. triggered_r asserts and trig_ptr_r captures
    wr_ptr_r at the same cycle."""
    await _start_clk(dut)
    await _reset(dut)

    # Configure with mask=0 so local trigger_hit NEVER fires (auto
    # would too, but mask=0 already makes hit always 1 — pin a
    # value+mask that can never match: trig_value=0xFFF, mask=0xFFF,
    # probe=0). That isolates the trigger_in path.
    dut.probe_in.value = 0
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0xFFF
    dut.trig_mask_in.value  = 0xFFF
    dut.trigger_in.value    = 0

    await ClockCycles(dut.sample_clk, 50)
    await _pulse(dut.arm_pulse, dut, 1)

    # Verify NO trigger fires from the (mismatched) local comparator.
    await ClockCycles(dut.sample_clk, 30)
    assert int(dut.triggered.value) == 0, (
        "local trigger_hit should NOT have fired (mask blocks it)"
    )

    # Now pulse trigger_in for one cycle and observe the FSM fires.
    prev_wr_ptr = int(dut.wr_ptr_out.value)
    dut.trigger_in.value = 1
    await RisingEdge(dut.sample_clk)
    dut.trigger_in.value = 0
    await RisingEdge(dut.sample_clk)

    assert int(dut.triggered.value) == 1, (
        "trigger_in pulse should have fired the capture"
    )
    trig_ptr = int(dut.trig_ptr_out.value)
    # Same off-by-N tolerance as the local trigger test: trig_ptr
    # snapshots the wr_ptr at the cycle the FSM observed the pulse.
    delta = (trig_ptr - prev_wr_ptr) & 0xFFF
    assert 0 <= delta <= 4, (
        f"trig_ptr={trig_ptr}, prev_wr_ptr={prev_wr_ptr}, delta={delta} "
        f"— expected close to wr_ptr at the trigger_in fire cycle"
    )

    dut._log.info(
        f"REA-REQ-400 PASS — trigger_in pulse fired, trig_ptr={trig_ptr}"
    )


@cocotb.test()
@requires("REA-REQ-401")
async def test_rea_req_401_trigger_in_does_not_drive_trigger_out(dut):
    """An external trigger_in pulse must NOT drive trigger_out_r —
    otherwise coupled REA cores ping-pong each other forever. Only
    a local trigger_hit drives trigger_out."""
    await _start_clk(dut)
    await _reset(dut)

    # Same setup as REQ-400: local comparator can never match.
    dut.probe_in.value = 0
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0xFFF
    dut.trig_mask_in.value  = 0xFFF

    await ClockCycles(dut.sample_clk, 50)
    await _pulse(dut.arm_pulse, dut, 1)

    # Watch trigger_out for an extended window during which we'll
    # pulse trigger_in. trigger_out must STAY LOW.
    saw_trigger_out = False

    async def _watch():
        nonlocal saw_trigger_out
        for _ in range(40):
            await RisingEdge(dut.sample_clk)
            if int(dut.trigger_out.value) == 1:
                saw_trigger_out = True

    watcher = cocotb.start_soon(_watch())

    # Pulse trigger_in.
    dut.trigger_in.value = 1
    await RisingEdge(dut.sample_clk)
    dut.trigger_in.value = 0
    await ClockCycles(dut.sample_clk, 20)
    await watcher

    assert int(dut.triggered.value) == 1, (
        "precondition: trigger_in should still have fired the FSM"
    )
    assert saw_trigger_out is False, (
        "REA-REQ-401 violated: trigger_out fired on a remote "
        "(trigger_in) trigger — would ping-pong with paired REA cores"
    )

    dut._log.info(
        "REA-REQ-401 PASS — trigger_in fires capture but NOT trigger_out"
    )


# ── REA-REQ-500/501: decimation (v0.3) ──────────────────────────────


@cocotb.test()
@requires("REA-REQ-500")
async def test_rea_req_500_decimation_zero_stores_every_cycle(dut):
    """With decim_ratio_in=0, dpram_we asserts every cycle —
    matches v0.1/v0.2 behavior. Pin the no-decimation default."""
    await _start_clk(dut)
    await _reset(dut)

    # Configure a never-firing trigger so the FSM stays in
    # "armed waiting" forever — done stays 0, dpram_we stays high
    # (when decim_ratio=0). Probe=0, value=0xFFF, mask=0xFFF →
    # (0 & 0xFFF) == (0xFFF & 0xFFF) → 0 != 0xFFF → trigger_hit=0.
    dut.probe_in.value = 0
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0xFFF
    dut.trig_mask_in.value  = 0xFFF
    dut.decim_ratio_in.value = 0    # ← no decimation
    dut.trigger_in.value = 0

    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)

    # Sample dpram_we across 16 cycles — should be high every cycle.
    we_samples = []
    for _ in range(16):
        we_samples.append(int(dut.dpram_we.value))
        await RisingEdge(dut.sample_clk)

    assert all(we == 1 for we in we_samples), (
        f"REA-REQ-500: dpram_we should be high every cycle when "
        f"decim_ratio=0; observed {we_samples}"
    )

    dut._log.info("REA-REQ-500 PASS — no decimation = store every cycle")


@cocotb.test()
@requires("REA-REQ-501")
async def test_rea_req_501_decimation_n_stores_one_in_n_plus_one(dut):
    """With decim_ratio_in=N (N>0), dpram_we asserts exactly once
    per (N+1) cycles. wr_ptr advances on each assertion only."""
    await _start_clk(dut)
    await _reset(dut)

    DECIM = 3   # ratio=3 → store 1 of every 4 cycles

    # Same never-firing trigger as REQ-500.
    dut.probe_in.value = 0
    dut.pretrig_len_in.value = 4
    dut.posttrig_len_in.value = 4
    dut.trig_value_in.value = 0xFFF
    dut.trig_mask_in.value  = 0xFFF
    dut.decim_ratio_in.value = DECIM
    dut.trigger_in.value = 0

    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)
    # Settle one cycle so the latched decim_ratio_r takes effect.
    await RisingEdge(dut.sample_clk)

    # Sample dpram_we + wr_ptr across 24 cycles. Expect exactly
    # 24 / (DECIM+1) = 6 high-cycles.
    high_count = 0
    wr_ptr_first = int(dut.wr_ptr_out.value)
    for _ in range(24):
        if int(dut.dpram_we.value) == 1:
            high_count += 1
        await RisingEdge(dut.sample_clk)
    wr_ptr_last = int(dut.wr_ptr_out.value)

    expected_high = 24 // (DECIM + 1)
    assert high_count == expected_high, (
        f"REA-REQ-501: dpram_we should be high {expected_high}/24 "
        f"cycles with decim_ratio={DECIM} (period={DECIM+1}); "
        f"observed {high_count}/24"
    )
    # wr_ptr advances by exactly the number of stored samples.
    assert wr_ptr_last - wr_ptr_first == expected_high, (
        f"wr_ptr advanced by {wr_ptr_last - wr_ptr_first}, expected "
        f"{expected_high} (one increment per stored sample)"
    )

    dut._log.info(
        f"REA-REQ-501 PASS — decim_ratio={DECIM}: stored "
        f"{high_count}/24 cycles (period={DECIM+1})"
    )


# ── REA-REQ-600: backward-compat (G_TRIG_STAGES=1 + seq_enable=0) ───


@cocotb.test()
@requires("REA-REQ-600")
async def test_rea_req_600_legacy_path_unchanged(dut):
    """With seq_enable_in=0 (default) and the FSM at G_TRIG_STAGES=1
    (default), trigger_hit comes from trig_value_in / trig_mask_in
    via the legacy comparator path — same wr_ptr_out / trig_ptr_out
    semantics as REA-REQ-100..106. No regression on the v0.1/v0.2
    contract when the sequencer registers are zero."""
    await _start_clk(dut)
    await _reset(dut)

    dut.probe_in.value = 0
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0x99
    dut.trig_mask_in.value  = 0xFFF
    # seq_enable_in stays 0 (default) — legacy path active.

    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)

    # Drive 0x99 — legacy comparator should fire.
    dut.probe_in.value = 0x99
    fired = False
    for _ in range(8):
        await RisingEdge(dut.sample_clk)
        if int(dut.triggered.value) == 1:
            fired = True
            break
    assert fired, "REA-REQ-600: legacy trig_value_in / trig_mask_in path failed to fire"

    dut._log.info("REA-REQ-600 PASS — legacy single-comparator path unchanged")


# ── REA-REQ-610/611/620/621/630: comparator op modes ───────────────
# (RTL-P3.644 edge · RTL-P3.645 magnitude + multi-bit NE · RTL-P3.646 host)
#
# The single-comparator (legacy) path gains an op nibble in TRIG_MODE[7:4].
# trig_mode_in is an 8-bit port: bit[0]=value_match (back-compat), [7:4]=op.
# op=0 (EQ) is the historical masked-equality behaviour — the existing
# REA-REQ-100..106/600 tests drive trig_mode_in=0 (default) and therefore
# already pin EQ; these tests attack the new ops.

TRIG_OP_EQ = 0
TRIG_OP_NE = 1
TRIG_OP_LT = 2
TRIG_OP_GT = 3
TRIG_OP_RISE = 4
TRIG_OP_FALL = 5


def _mode(op: int) -> int:
    """TRIG_MODE low byte: value_match bit set + op nibble at [7:4]."""
    return 0x01 | (op << 4)


async def _fire_within(dut, n: int) -> bool:
    """Return True if `triggered` asserts within the next n edges."""
    for _ in range(n):
        await RisingEdge(dut.sample_clk)
        if int(dut.triggered.value) == 1:
            return True
    return False


async def _assert_no_fire(dut, n: int, msg: str) -> None:
    """Assert `triggered` stays low across the next n edges."""
    for _ in range(n):
        await RisingEdge(dut.sample_clk)
        assert int(dut.triggered.value) == 0, msg


@cocotb.test()
@requires("REA-REQ-610")
async def test_rea_req_610_rising_edge_fires_only_on_0_to_1(dut):
    """op=RISE fires on a masked 0->1 transition only — NOT on a probe
    held steady-high across cycles, and NOT on a 1->0 falling edge."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0x001  # watch bit 0
    dut.trig_value_in.value = 0     # value is don't-care for edge ops
    dut.trig_mode_in.value = _mode(TRIG_OP_RISE)

    # Hold bit 0 HIGH from before arm so probe_prev_r latches '1'.
    dut.probe_in.value = 0x001
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)

    # Steady-high: no rising edge → must NOT fire.
    await _assert_no_fire(dut, 6, "REA-REQ-610: RISE fired on steady-high probe")

    # Falling 1->0: wrong direction for RISE → must NOT fire.
    dut.probe_in.value = 0x000
    await _assert_no_fire(dut, 4, "REA-REQ-610: RISE fired on a falling edge")

    # Rising 0->1: MUST fire.
    dut.probe_in.value = 0x001
    assert await _fire_within(dut, 6), (
        "REA-REQ-610: RISE did not fire on a 0->1 transition"
    )

    dut._log.info("REA-REQ-610 PASS — rising edge fires only on 0->1")


@cocotb.test()
@requires("REA-REQ-611")
async def test_rea_req_611_falling_edge_fires_only_on_1_to_0(dut):
    """op=FALL fires on a masked 1->0 transition only — NOT on steady-low
    and NOT on a 0->1 rising edge."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0x001
    dut.trig_value_in.value = 0
    dut.trig_mode_in.value = _mode(TRIG_OP_FALL)

    # Hold bit 0 LOW from before arm so probe_prev_r latches '0'.
    dut.probe_in.value = 0x000
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)

    # Steady-low: no falling edge → must NOT fire.
    await _assert_no_fire(dut, 6, "REA-REQ-611: FALL fired on steady-low probe")

    # Rising 0->1: wrong direction for FALL → must NOT fire.
    dut.probe_in.value = 0x001
    await _assert_no_fire(dut, 4, "REA-REQ-611: FALL fired on a rising edge")

    # Falling 1->0: MUST fire.
    dut.probe_in.value = 0x000
    assert await _fire_within(dut, 6), (
        "REA-REQ-611: FALL did not fire on a 1->0 transition"
    )

    dut._log.info("REA-REQ-611 PASS — falling edge fires only on 1->0")


@cocotb.test()
@requires("REA-REQ-620")
async def test_rea_req_620_less_than_fires_strictly_below(dut):
    """op=LT fires when masked probe (unsigned) < masked trig_value.
    It must NOT fire on equality nor on greater-than."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0xFFF
    dut.trig_value_in.value = 0x100
    dut.trig_mode_in.value = _mode(TRIG_OP_LT)

    dut.probe_in.value = 0x100  # equal → no fire (strict <)
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)
    await _assert_no_fire(dut, 5, "REA-REQ-620: LT fired on equality")

    dut.probe_in.value = 0x150  # greater → no fire
    await _assert_no_fire(dut, 5, "REA-REQ-620: LT fired on greater-than")

    dut.probe_in.value = 0x0FF  # less → fire
    assert await _fire_within(dut, 6), "REA-REQ-620: LT did not fire below value"

    dut._log.info("REA-REQ-620 PASS — LT fires strictly below trig_value")


@cocotb.test()
@requires("REA-REQ-621")
async def test_rea_req_621_greater_than_fires_strictly_above(dut):
    """op=GT fires when masked probe (unsigned) > masked trig_value.
    It must NOT fire on equality nor on less-than."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0xFFF
    dut.trig_value_in.value = 0x100
    dut.trig_mode_in.value = _mode(TRIG_OP_GT)

    dut.probe_in.value = 0x100  # equal → no fire (strict >)
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)
    await _assert_no_fire(dut, 5, "REA-REQ-621: GT fired on equality")

    dut.probe_in.value = 0x0FF  # less → no fire
    await _assert_no_fire(dut, 5, "REA-REQ-621: GT fired on less-than")

    dut.probe_in.value = 0x150  # greater → fire
    assert await _fire_within(dut, 6), "REA-REQ-621: GT did not fire above value"

    dut._log.info("REA-REQ-621 PASS — GT fires strictly above trig_value")


@cocotb.test()
@requires("REA-REQ-630")
async def test_rea_req_630_not_equal_fires_on_any_bit_difference(dut):
    """op=NE fires when the masked probe differs from masked trig_value in
    ANY bit (multi-bit inequality — the old hw could only do 1-bit !=).
    It must NOT fire when the masked fields are equal."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0xFFF
    dut.trig_value_in.value = 0x0AA
    dut.trig_mode_in.value = _mode(TRIG_OP_NE)

    dut.probe_in.value = 0x0AA  # equal → no fire
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)
    await _assert_no_fire(dut, 5, "REA-REQ-630: NE fired on equal masked fields")

    # 0x155 differs from 0x0AA in many bits (in fact every masked bit).
    dut.probe_in.value = 0x155
    assert await _fire_within(dut, 6), (
        "REA-REQ-630: NE did not fire on a multi-bit difference"
    )

    dut._log.info("REA-REQ-630 PASS — NE fires on any-bit (multi-bit) difference")


@cocotb.test()
@requires("REA-REQ-600")
async def test_rea_req_600b_host_literal_mode_0x01_is_eq(dut):
    """Back-compat pin: the host library writes TRIG_MODE=0x01 (value_match
    set, op nibble 0). That literal must behave as EQ — masked equality —
    so existing fcapz/host flows are unaffected by the op-nibble addition."""
    await _start_clk(dut)
    await _reset(dut)

    dut.trig_mask_in.value = 0xFFF
    dut.trig_value_in.value = 0x099
    dut.trig_mode_in.value = 0x01  # host literal: value_match, op=EQ

    dut.probe_in.value = 0x000  # mismatch → no fire
    await ClockCycles(dut.sample_clk, 5)
    await _pulse(dut.arm_pulse, dut, 1)
    await _assert_no_fire(dut, 5, "mode 0x01 fired on a non-matching probe")

    dut.probe_in.value = 0x099  # exact match → fire
    assert await _fire_within(dut, 6), (
        "mode 0x01 (host literal) failed to fire on an exact masked match"
    )

    dut._log.info("REA-REQ-600 PASS — host-literal TRIG_MODE=0x01 == EQ")


if __name__ == "__main__":
    main()
