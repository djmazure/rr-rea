# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.29 — the cross-core trigger, two rr_rea_top cores through
rr_rea_trig_xbar (REA-REQ-975, REA-REQ-976, REA-REQ-401).

Before REA-P2.29 rr_rea_top had no trigger_i port: the capture FSM's
trigger_i sat at its default '0', so rr_rea_trig_xbar's pulse reached no core
and "freeze several clock domains together" could not happen, while AGENTS.md
advertised it. These tests couple two cores the way SPEC tells an integrator
to (fixtures/rr_rea_cross_trigger_harness.vhd) on two unrelated sample clocks.

Oracles are the testbench's own record of what it put on each probe_i: a
9-bit counter per core (bit 8 always set, so 0x000 never appears and core B's
pattern can never match), and the core-B value present at the moment core A
fired. Nothing is derived from the DUT's outputs.
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

from test_rea_top import _jtag_read, _jtag_write  # noqa: E402

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

DEPTH = 256
GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": DEPTH, "G_TIMESTAMP_W": 0}
_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_START_PTR = 0xC8
ADDR_DATA_BASE = 0x100

CTRL_ARM = 1 << 0
ARMED, TRIGGERED, DONE = 1 << 0, 1 << 1, 1 << 2
FORCED = 1 << 9
STATUS_CAPTURE_BITS = ARMED | TRIGGERED | DONE | FORCED

CLK_A_NS = 8.0      # 125 MHz
CLK_B_NS = 13.0     # ~77 MHz, unrelated to A
TCK_NS = 25.0
PRETRIG, POSTTRIG = 32, 32
TRIG_A = 0x1A5      # a counter value core A's pattern matches
NEVER = 0x000       # bit 8 is always set, so core B's pattern never matches
# Core-B samples from the moment core A's trigger_o flips to core B's trigger
# cell: rr_rea_pulse_xfer (two sync flops and an edge flop on clk_b) and the
# FSM edge, minus rr_rea_top's probe register. Measured 2 on this harness.
CROSS_LAG_MAX = 4


def main() -> None:
    run_simulation(
        top_level="rr_rea_cross_trigger_harness",
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
                str(_RTL_DIR / "rr_rea_trig_xbar.vhd"),
                str(_FIX / "rr_rea_cross_trigger_harness.vhd"),
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


class Tap:
    """One core's register door, under the names test_rea_top's scan
    helpers drive (tck and the TAP reset are shared by both cores)."""

    def __init__(self, dut, prefix: str) -> None:
        self.tck_i = dut.tck_i
        for name in ("tdi_i", "tdo_o", "capture_i", "shift_en_i", "update_i", "sel_i"):
            setattr(self, name, getattr(dut, f"{prefix}_{name}"))


class Domain:
    """Drives one core's probe with a free-running 9-bit counter (bit 8 set)
    on the falling edge, and records on each rising edge what that core's
    trigger outputs and xbar input did."""

    def __init__(self, dut, clk, probe, toggle, pulse, xbar_in) -> None:
        self.clk, self.probe = clk, probe
        self.toggle, self.pulse, self.xbar_in = toggle, pulse, xbar_in
        self.count = 0
        self.presented = 0
        self.cycle = 0
        self.toggle_cycles: list[int] = []    # cycles trigger_o was seen to flip
        self.pulse_cycles: list[int] = []     # cycles trigger_pulse_o read 1
        self.xbar_in_cycles: list[int] = []   # cycles the xbar pulse read 1
        self.on_toggle = None                 # callback(cycle) on a flip
        self._last_toggle = 0

    async def drive(self) -> None:
        while True:
            await FallingEdge(self.clk)
            self.presented = 0x100 | (self.count & 0xFF)
            self.probe.value = self.presented
            self.count += 1

    @staticmethod
    def _bit(handle) -> int:
        return int(handle.value) if handle.value.is_resolvable else 0

    async def watch(self) -> None:
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            self.cycle += 1
            level = self._bit(self.toggle)
            if level != self._last_toggle:
                self.toggle_cycles.append(self.cycle)
                if self.on_toggle is not None:
                    self.on_toggle(self.cycle)
            self._last_toggle = level
            if self._bit(self.pulse):
                self.pulse_cycles.append(self.cycle)
            if self._bit(self.xbar_in):
                self.xbar_in_cycles.append(self.cycle)


async def _start(dut) -> tuple[Domain, Domain]:
    cocotb.start_soon(Clock(dut.clk_a_i, CLK_A_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_b_i, CLK_B_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_NS, unit="ns").start())
    dut.rst_a_i.value = 1
    dut.rst_b_i.value = 1
    dut.arst_i.value = 1
    for p in ("a", "b"):
        for name in ("tdi_i", "capture_i", "shift_en_i", "update_i", "sel_i"):
            getattr(dut, f"{p}_{name}").value = 0
    dut.probe_a_i.value = 0
    dut.probe_b_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.rst_a_i.value = 0
    dut.rst_b_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)
    a = Domain(dut, dut.clk_a_i, dut.probe_a_i, dut.a_trigger_o,
               dut.a_trigger_pulse_o, dut.xbar_pulse_a_o)
    b = Domain(dut, dut.clk_b_i, dut.probe_b_i, dut.b_trigger_o,
               dut.b_trigger_pulse_o, dut.xbar_pulse_b_o)
    for d in (a, b):
        cocotb.start_soon(d.drive())
        cocotb.start_soon(d.watch())
    # Fill both rings with counter values before any capture.
    await ClockCycles(dut.clk_b_i, 2 * DEPTH)
    return a, b


async def _configure_and_arm(tap: Tap, value: int, who: str) -> None:
    await _jtag_write(tap, ADDR_PRETRIG, PRETRIG)
    await _jtag_write(tap, ADDR_POSTTRIG, POSTTRIG)
    await _jtag_write(tap, ADDR_TRIG_MODE, 0x1)         # value_match, op EQ
    await _jtag_write(tap, ADDR_TRIG_VALUE, value)
    await _jtag_write(tap, ADDR_TRIG_MASK, 0xFFF)
    await _jtag_write(tap, ADDR_CTRL, CTRL_ARM)
    for _ in range(50):
        if await _jtag_read(tap, ADDR_STATUS) & ARMED:
            return
    raise AssertionError(f"core {who} never reported ARMED after the arm write")


async def _wait_done(tap: Tap, req: str, who: str, polls: int = 120) -> int:
    status = 0
    for _ in range(polls):
        status = await _jtag_read(tap, ADDR_STATUS)
        if status & DONE:
            return status
        await ClockCycles(tap.tck_i, 4)
    raise AssertionError(
        f"{req}: core {who} never triggered (STATUS=0x{status:03X}): core A "
        f"fired, but nothing reached core {who}'s capture FSM through "
        "rr_rea_trig_xbar and rr_rea_top's trigger_i")


async def _window(tap: Tap) -> list[int]:
    start = await _jtag_read(tap, ADDR_START_PTR) & (DEPTH - 1)
    return [await _jtag_read(tap, ADDR_DATA_BASE + 4 * ((start + i) % DEPTH)) & 0xFFF
            for i in range(PRETRIG + 1 + POSTTRIG)]


async def _fire_a_with_b_armed(dut) -> tuple[Domain, Domain, list[int]]:
    """Arm B on a pattern that never matches, then arm A on one that does.
    Returns the domains and the core-B values present when A's trigger_o
    flipped (one per flip)."""
    a, b = await _start(dut)
    b_at_a_fire: list[int] = []
    a.on_toggle = lambda _cycle: b_at_a_fire.append(b.presented)
    await _configure_and_arm(Tap(dut, "b"), NEVER, "B")
    await _configure_and_arm(Tap(dut, "a"), TRIG_A, "A")
    return a, b, b_at_a_fire


@cocotb.test()
@requires("REA-REQ-975", "REA-REQ-401")
async def test_rea_req_975_core_a_trigger_freezes_core_b(dut):
    """Core A triggers on its pattern; core B, armed on a pattern that never
    matches, triggers through rr_rea_trig_xbar within CROSS_LAG_MAX of its
    own samples of that moment. Its window is contiguous around that cell,
    STATUS says triggered and done (not forced), and B's trigger never comes
    back to A (REA-REQ-401)."""
    req = "REA-REQ-975"
    a, b, b_at_a_fire = await _fire_a_with_b_armed(dut)
    tap_a, tap_b = Tap(dut, "a"), Tap(dut, "b")

    status_a = await _wait_done(tap_a, req, "A")
    assert status_a & STATUS_CAPTURE_BITS == TRIGGERED | DONE, (
        f"{req}: core A STATUS=0x{status_a:03X}, want triggered|done")
    window_a = await _window(tap_a)
    assert window_a[PRETRIG] == TRIG_A, (
        f"{req}: core A trigger cell 0x{window_a[PRETRIG]:03X}, want its "
        f"pattern 0x{TRIG_A:03X}")
    assert len(b_at_a_fire) == 1, (
        f"{req}: core A's trigger_o flipped {len(b_at_a_fire)} times, want 1")

    status_b = await _wait_done(tap_b, req, "B")
    assert status_b & STATUS_CAPTURE_BITS == TRIGGERED | DONE, (
        f"{req}: core B STATUS=0x{status_b:03X}, want triggered|done "
        "(a cross-core trigger is not a force)")
    assert len(b.xbar_in_cycles) == 1, (
        f"{req}: rr_rea_trig_xbar pulsed core B's trigger_i "
        f"{len(b.xbar_in_cycles)} times for one fire of core A")
    window_b = await _window(tap_b)
    assert all(cell & 0x100 for cell in window_b), (
        f"{req}: core B window holds a cell its probe never drove: {window_b}")
    gaps = [i for i in range(1, len(window_b))
            if (window_b[i] - window_b[i - 1]) & 0xFF != 1]
    assert not gaps, f"{req}: core B window not contiguous at {gaps}: {window_b}"
    lag = (window_b[PRETRIG] - b_at_a_fire[0]) & 0xFF
    dut._log.info(f"{req}: core B trigger cell 0x{window_b[PRETRIG]:03X}, "
                  f"{lag} core-B samples after core A fired")
    assert lag <= CROSS_LAG_MAX, (
        f"{req}: core B trigger cell 0x{window_b[PRETRIG]:03X} is {lag} core-B "
        f"samples from 0x{b_at_a_fire[0]:03X}, the core-B sample present when "
        f"core A fired (want <= {CROSS_LAG_MAX}): not the same moment")

    # REA-REQ-401: B's fire came from trigger_i, so it does not flip B's
    # trigger_o, and nothing travels back to A.
    assert b.toggle_cycles == [], (
        f"REA-REQ-401: core B's trigger_o flipped at core-B cycles "
        f"{b.toggle_cycles} for a trigger_i fire (the xbar would ping-pong)")
    assert a.xbar_in_cycles == [], (
        f"REA-REQ-401: rr_rea_trig_xbar pulsed core A's trigger_i at "
        f"{a.xbar_in_cycles}, but core B never fired locally")


@cocotb.test()
@requires("REA-REQ-976")
async def test_rea_req_976_trigger_pulse_o_one_cycle_per_local_fire(dut):
    """trigger_pulse_o is high for exactly one sample cycle per local fire,
    the cycle before trigger_o flips, and stays low on a core whose capture
    was not triggered by its own pattern."""
    req = "REA-REQ-976"
    a, b, _ = await _fire_a_with_b_armed(dut)
    await _wait_done(Tap(dut, "a"), req, "A")
    await ClockCycles(dut.clk_b_i, 4 * DEPTH)

    assert len(a.toggle_cycles) == 1, (
        f"{req}: core A's trigger_o flipped at {a.toggle_cycles}, want once")
    assert a.pulse_cycles == [a.toggle_cycles[0] - 1], (
        f"{req}: core A's trigger_pulse_o read 1 at cycles {a.pulse_cycles}; "
        f"want exactly one cycle, {a.toggle_cycles[0] - 1}, the cycle before "
        f"trigger_o flipped at {a.toggle_cycles[0]}")
    assert b.pulse_cycles == [], (
        f"{req}: core B never matched its own pattern, yet its "
        f"trigger_pulse_o read 1 at cycles {b.pulse_cycles}")


if __name__ == "__main__":
    main()
