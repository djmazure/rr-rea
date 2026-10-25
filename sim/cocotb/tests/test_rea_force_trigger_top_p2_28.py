# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.28 — force-trigger through rr_rea_top's JTAG door (REA-REQ-972..974).

The Vivado ILA "Run Trigger Immediate". Before REA-P2.28 CTRL had only ARM
(bit 0) and RESET (bit 1): an armed core waiting on a pattern that never came
could not be made to capture; the host had to reset, reprogram and re-arm,
losing the moment it wanted to see. These tests drive the path a host uses:
CTRL[2] over JTAG, the toggle crossing into sample_clk, the FSM's fire, and
STATUS[9] crossing back.

Oracles are the testbench's own record of what it put on probe_i:
  * a 9-bit counter (bit 8 always set, so never 0) whose pattern-trigger
    value 0 can never match: only the force can end the wait;
  * a constant STALE word left in the ring by an earlier capture, so the
    cells written after the arm are countable in the read-back window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

_SIM_ROOT = str(Path(__file__).resolve().parents[2])
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)

from test_rea_top import _jtag_read, _jtag_write, _reset  # noqa: E402

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

DEPTH = 1024
GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": DEPTH,
    "G_TIMESTAMP_W": 0,
    "G_NUM_CHAN": 1,
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
ADDR_START_PTR = 0xC8
ADDR_PRETRIG_VALID = 0xF0
ADDR_DATA_BASE = 0x100

CTRL_ARM = 1 << 0
CTRL_FORCE_TRIG = 1 << 2          # C_CTRL_BIT_FORCE_TRIG
ARMED, TRIGGERED, DONE = 1 << 0, 1 << 1, 1 << 2
FORCED = 1 << 9                   # C_STATUS_BIT_FORCED
STATUS_CAPTURE_BITS = ARMED | TRIGGERED | DONE | FORCED
VERSION_V0_17 = 0x52454111        # tier byte stays odd (REA-REQ-806)

TCK_NS = 25.0
STALE = 0xEEE                     # never a counter value (bit 8 set, < 0x200)
# CDC + edge detect + FSM edge, in sample cycles, from the CTRL Update-DR edge
# to the cycle the force lands. rr_rea_pulse_xfer is a 2-flop sync plus an
# edge flop; the FSM acts on the next edge.
FORCE_LAND_MAX = 8


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


class Probe:
    """Drives probe_i on the falling sample edge (a free-running 9-bit
    counter, or a constant) and counts local fires. rr_rea_top's trigger_o
    is a toggle (it flips once per local fire), so a fire is a change."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.count = 0
        self.constant: int | None = None
        self.presented = 0
        self.trigger_o_cycles = 0   # trigger_o toggles = local fires
        self.trigger_seen = False
        self._last_trigger_o = 0

    @staticmethod
    def word(count: int) -> int:
        return 0x100 | (count & 0xFF)

    async def run(self) -> None:
        dut = self.dut
        while True:
            await FallingEdge(dut.sample_clk_i)
            value = self.constant if self.constant is not None else self.word(self.count)
            dut.probe_i.value = value
            self.presented = value
            self.count += 1
            await ReadOnly()
            level = int(dut.trigger_o.value) if dut.trigger_o.value.is_resolvable else 0
            if level != self._last_trigger_o:
                self.trigger_o_cycles += 1
                self.trigger_seen = True
            self._last_trigger_o = level


async def _start(dut, sample_ns: float) -> Probe:
    cocotb.start_soon(Clock(dut.sample_clk_i, sample_ns, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_NS, unit="ns").start())
    await _reset(dut)
    probe = Probe(dut)
    cocotb.start_soon(probe.run())
    return probe


async def _status(dut) -> int:
    return await _jtag_read(dut, ADDR_STATUS)


async def _configure(dut, pretrig: int, posttrig: int, value: int) -> None:
    await _jtag_write(dut, ADDR_PRETRIG, pretrig)
    await _jtag_write(dut, ADDR_POSTTRIG, posttrig)
    await _jtag_write(dut, ADDR_TRIG_MODE, 0x1)       # value_match, op EQ
    await _jtag_write(dut, ADDR_TRIG_VALUE, value)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0xFFF)


async def _arm_and_see_armed(dut, req: str) -> None:
    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    for _ in range(50):
        if await _status(dut) & ARMED:
            return
    raise AssertionError(f"{req}: core never reported ARMED after the arm write")


async def _wait_done(dut, req: str, polls: int = 200) -> int:
    status = 0
    for _ in range(polls):
        status = await _status(dut)
        if status & DONE:
            return status
        await ClockCycles(dut.tck_i, 4)
    raise AssertionError(f"{req}: capture never reached DONE (STATUS=0x{status:03X})")


async def _cell(dut, start: int, index: int) -> int:
    return await _jtag_read(dut, ADDR_DATA_BASE + 4 * ((start + index) % DEPTH)) & 0xFFF


async def _snapshot(dut, pretrig: int) -> dict:
    start = await _jtag_read(dut, ADDR_START_PTR) & (DEPTH - 1)
    return {
        "status": await _status(dut) & STATUS_CAPTURE_BITS,
        "start": start,
        "pretrig_valid": await _jtag_read(dut, ADDR_PRETRIG_VALID),
        "trigger_cell": await _cell(dut, start, pretrig),
    }


@cocotb.test()
@requires("REA-REQ-972", "REA-REQ-974")
async def test_rea_req_972_974_force_while_armed(dut):
    """An armed core whose pattern never matches is still waiting; CTRL[2]
    makes it trigger on the sample present when the command lands. The
    window is PRETRIG contiguous samples, that trigger cell, then POSTTRIG
    samples. PRETRIG_VALID = PRETRIG (armed long before), STATUS reads
    triggered|done|forced, and trigger_o never pulses."""
    req = "REA-REQ-972/974"
    pretrig, posttrig = 40, 40
    probe = await _start(dut, sample_ns=8.0)
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)
    await _configure(dut, pretrig, posttrig, value=0x000)   # never matches
    await _arm_and_see_armed(dut, req)
    await ClockCycles(dut.sample_clk_i, 4 * pretrig)
    status = await _status(dut)
    assert status & STATUS_CAPTURE_BITS == ARMED, (
        f"{req}: before the force the core should be armed and waiting, "
        f"STATUS=0x{status:03X}")

    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    at_update = probe.presented               # the sample at the Update-DR edge
    status = await _wait_done(dut, req)
    assert status & STATUS_CAPTURE_BITS == TRIGGERED | DONE | FORCED, (
        f"{req}: after a forced capture STATUS=0x{status:03X}, want "
        f"triggered|done|forced (0x{TRIGGERED | DONE | FORCED:03X})")
    assert probe.trigger_o_cycles == 0, (
        f"{req}: a forced trigger toggled trigger_o {probe.trigger_o_cycles} times")

    start = await _jtag_read(dut, ADDR_START_PTR) & (DEPTH - 1)
    window = [await _cell(dut, start, i) for i in range(pretrig + 1 + posttrig)]
    assert all(cell & 0x100 for cell in window), (
        f"{req}: window holds a cell the probe never drove: {window}")
    gaps = [i for i in range(1, len(window))
            if (window[i] - window[i - 1]) & 0xFF != 1]
    assert not gaps, f"{req}: window is not contiguous at offsets {gaps}: {window}"
    lag = (window[pretrig] - at_update) & 0xFF
    assert lag <= FORCE_LAND_MAX, (
        f"{req}: trigger cell 0x{window[pretrig]:03X} is {lag} samples after "
        f"the force command (0x{at_update:03X}); it should be the sample "
        f"present when the command landed (<= {FORCE_LAND_MAX})")
    pv = await _jtag_read(dut, ADDR_PRETRIG_VALID)
    assert pv == pretrig, f"{req}: PRETRIG_VALID={pv}, want {pretrig}"
    assert await _jtag_read(dut, ADDR_VERSION) == VERSION_V0_17, (
        f"{req}: VERSION is not the v0.17 tier that advertises CTRL[2]")


@cocotb.test()
@requires("REA-REQ-973")
async def test_rea_req_973_force_idle_or_triggered_is_noop(dut):
    """A force on a core that never armed, on one in its post-trigger window,
    and on one that is done changes nothing: no status bit, same trigger
    cell, START_PTR and PRETRIG_VALID. A later arm captures on its pattern,
    so no force was held pending."""
    req = "REA-REQ-973"
    pretrig, posttrig = 16, 600            # 600 x 8 ns >> one CTRL write
    probe = await _start(dut, sample_ns=8.0)
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    # 1. Never armed.
    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    await ClockCycles(dut.sample_clk_i, 4 * FORCE_LAND_MAX)
    status = await _status(dut)
    assert status & STATUS_CAPTURE_BITS == 0, (
        f"{req}: a force on a never-armed core set STATUS=0x{status:03X}")

    # 2. Triggered, inside the post-trigger window: pattern capture, then a
    # force issued the moment trigger_o fires.
    pattern = Probe.word(probe.count + 300)
    await _configure(dut, pretrig, posttrig, value=pattern)
    await _arm_and_see_armed(dut, req)
    while not probe.trigger_seen:
        await RisingEdge(dut.sample_clk_i)
    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    await _wait_done(dut, req)
    first = await _snapshot(dut, pretrig)
    assert first["status"] == TRIGGERED | DONE, (
        f"{req}: a force after the pattern fired left STATUS=0x{first['status']:03X}, "
        f"want triggered|done (0x{TRIGGERED | DONE:03X}), not forced")
    assert first["trigger_cell"] == pattern, (
        f"{req}: a force in the post-trigger window moved the trigger cell to "
        f"0x{first['trigger_cell']:03X}, want the pattern 0x{pattern:03X}")
    assert first["pretrig_valid"] == pretrig, (
        f"{req}: PRETRIG_VALID={first['pretrig_valid']}, want {pretrig}")

    # 3. Done.
    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    await ClockCycles(dut.sample_clk_i, 4 * FORCE_LAND_MAX)
    after = await _snapshot(dut, pretrig)
    assert after == first, (
        f"{req}: a force on a done core changed the capture: {first} -> {after}")

    # 4. A later arm captures on its pattern: nothing was left pending.
    pattern = Probe.word(probe.count + 300)
    await _jtag_write(dut, ADDR_TRIG_VALUE, pattern)
    await _arm_and_see_armed(dut, req)
    await _wait_done(dut, req)
    last = await _snapshot(dut, pretrig)
    assert last["status"] == TRIGGERED | DONE and last["trigger_cell"] == pattern, (
        f"{req}: the arm after the ignored forces did not capture on its "
        f"pattern 0x{pattern:03X}: {last}")
    assert probe.trigger_o_cycles == 2, (
        f"{req}: trigger_o toggled {probe.trigger_o_cycles} times, want 2 "
        "(the two pattern fires)")


@cocotb.test()
@requires("REA-REQ-972", "REA-REQ-974")
async def test_rea_req_972_force_soon_after_arm_keeps_every_post_arm_cell(dut):
    """A force that lands fewer than PRETRIG samples after the arm. The ring
    still holds an earlier capture's STALE word, so the cells written since
    the arm are exactly the non-STALE ones before the trigger cell, and
    PRETRIG_VALID must count every one of them (the host trims the rest).
    The sample clock is slowed so one CTRL write is ~13 sample cycles."""
    req = "REA-REQ-972"
    pretrig, posttrig = 48, 16
    probe = await _start(dut, sample_ns=100.0)

    # An earlier capture of a constant word: every recent cell is STALE.
    probe.constant = STALE
    await _configure(dut, pretrig, posttrig, value=0x000)
    await ClockCycles(dut.sample_clk_i, 2 * pretrig)
    await _arm_and_see_armed(dut, req)
    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    await _wait_done(dut, req)

    # The capture under test: counter on the probe, arm, force at once.
    probe.constant = None
    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    await _jtag_write(dut, ADDR_CTRL, CTRL_FORCE_TRIG)
    status = await _wait_done(dut, req)
    assert status & STATUS_CAPTURE_BITS == TRIGGERED | DONE | FORCED, (
        f"REA-REQ-974: STATUS=0x{status:03X} after the forced capture")

    start = await _jtag_read(dut, ADDR_START_PTR) & (DEPTH - 1)
    pre = [await _cell(dut, start, i) for i in range(pretrig + 1)]
    fresh = [i for i, cell in enumerate(pre) if cell != STALE]
    assert fresh and fresh[-1] == pretrig, (
        f"{req}: the trigger cell is STALE, the force did not land on a "
        f"live sample: {pre}")
    first_fresh = fresh[0]
    assert fresh == list(range(first_fresh, pretrig + 1)), (
        f"{req}: STALE and fresh cells interleave in the pre-trigger window: {pre}")
    post_arm = pretrig - first_fresh
    assert 0 < post_arm < pretrig, (
        f"{req}: {post_arm} post-arm cells; the force should land fewer than "
        f"PRETRIG={pretrig} samples after the arm for this test to mean anything")
    gaps = [i for i in range(first_fresh + 1, pretrig + 1)
            if (pre[i] - pre[i - 1]) & 0xFF != 1]
    assert not gaps, f"{req}: post-arm cells are not contiguous at {gaps}: {pre}"
    # The arm edge stores nothing (done still holds the ring that cycle), so
    # every fresh pre-trigger cell was counted after the arm.
    pv = await _jtag_read(dut, ADDR_PRETRIG_VALID)
    assert pv == post_arm, (
        f"{req}: PRETRIG_VALID={pv} but {post_arm} cells before the trigger "
        f"cell were written after the arm")


if __name__ == "__main__":
    main()
