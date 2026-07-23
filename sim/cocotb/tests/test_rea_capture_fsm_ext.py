# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — external board-pin trigger (RTL-P3.266).
#
# Covers REA-REQ-410..413 (see ../requirements.yml): the ext_trigger_in
# package-pin input, gated by TRIG_MODE ext_en[3]/ext_and[8], folds into the
# fire decision as OR (fire on either internal hit or pin) or AND (fire only
# when both). Distinct from the trig_xbar trigger_in pulse (REA-REQ-400/401),
# which stays an independent OR.
#
# Run via:  rr sim run --ip <rea-dir> test_rea_capture_fsm_ext

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
DERIVED_PIPE_STAGES = 4
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
CLK_PERIOD_NS = 8.0


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_ext",
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


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.sample_clk, CLK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.trigger_in.value = 0
    dut.pretrig_len_in.value = 8
    dut.posttrig_len_in.value = 8
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0
    dut.trig_mode_in.value = 0
    # RTL-P3.266 external-trigger inputs — default off each test.
    dut.ext_trigger_in.value = 0
    dut.ext_enable_in.value = 0
    dut.ext_and_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await ClockCycles(dut.sample_clk, 1)


async def _pulse(sig, dut, n_cycles: int = 1):
    sig.value = 1
    for _ in range(n_cycles):
        await RisingEdge(dut.sample_clk)
    sig.value = 0


def _never_match(dut):
    """Pin a value/mask the all-zero probe can never satisfy → local hit=0."""
    dut.probe_in.value = 0
    dut.trig_value_in.value = 0xFFF
    dut.trig_mask_in.value = 0xFFF


def _always_match(dut):
    """mask=0 → masked equality always true → local hit=1 every cycle."""
    dut.probe_in.value = 0
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0


# ── REA-REQ-410: ext OR — the pin fires capture on its own ───────────


@cocotb.test()
@requires("REA-REQ-410")
async def test_rea_req_410_ext_or_pin_fires(dut):
    """ext_enable=1, ext_and=0 (OR): with the local comparator masked off,
    raising ext_trigger_in alone fires the capture."""
    await _start_clk(dut)
    await _reset(dut)
    _never_match(dut)
    dut.ext_enable_in.value = 1
    dut.ext_and_in.value = 0

    await ClockCycles(dut.sample_clk, 20)
    await _pulse(dut.arm_pulse, dut, 1)
    await ClockCycles(dut.sample_clk, 20)
    assert int(dut.triggered.value) == 0, "no local match → must not fire yet"

    dut.ext_trigger_in.value = 1
    await ClockCycles(dut.sample_clk, DERIVED_PIPE_STAGES + 2)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, "ext pin (OR) should have fired"
    dut._log.info("REA-REQ-410 PASS — ext OR pin fired")


# ── REA-REQ-411: ext AND — fire ONLY when internal hit AND pin ───────


@cocotb.test()
@requires("REA-REQ-411")
async def test_rea_req_411_ext_and_requires_both(dut):
    """ext_enable=1, ext_and=1 (AND): a permanently-true local comparator
    must NOT fire while the pin is low, and MUST fire once the pin rises."""
    await _start_clk(dut)
    await _reset(dut)
    _always_match(dut)            # local hit = 1 every cycle
    dut.ext_enable_in.value = 1
    dut.ext_and_in.value = 1
    dut.ext_trigger_in.value = 0  # pin low → AND gate open

    await ClockCycles(dut.sample_clk, 20)
    await _pulse(dut.arm_pulse, dut, 1)
    await ClockCycles(dut.sample_clk, 20)
    assert int(dut.triggered.value) == 0, (
        "AND mode: local hit alone (pin low) must NOT fire"
    )

    # Raise the pin — now BOTH conditions hold → fire.
    dut.ext_trigger_in.value = 1
    await ClockCycles(dut.sample_clk, DERIVED_PIPE_STAGES + 2)
    await ReadOnly()
    assert int(dut.triggered.value) == 1, (
        "AND mode: local hit AND pin should have fired"
    )
    dut._log.info("REA-REQ-411 PASS — ext AND requires both")


# ── REA-REQ-412: ext disabled → pin ignored (back-compat) ────────────


@cocotb.test()
@requires("REA-REQ-412")
async def test_rea_req_412_ext_disabled_ignores_pin(dut):
    """ext_enable=0: ext_trigger_in is ignored entirely — a high pin must
    NOT fire when the local comparator can't match (proves the internal-only
    path is unchanged when the feature is off)."""
    await _start_clk(dut)
    await _reset(dut)
    _never_match(dut)
    dut.ext_enable_in.value = 0    # feature OFF
    dut.ext_trigger_in.value = 1   # pin held HIGH the whole time

    await ClockCycles(dut.sample_clk, 20)
    await _pulse(dut.arm_pulse, dut, 1)
    await ClockCycles(dut.sample_clk, 30)
    assert int(dut.triggered.value) == 0, (
        "ext disabled: a high pin must be ignored (no fire)"
    )
    dut._log.info("REA-REQ-412 PASS — disabled ext pin ignored")


# ── REA-REQ-413: AND-mode local-only does not pulse trigger_out ──────


@cocotb.test()
@requires("REA-REQ-413")
async def test_rea_req_413_and_mode_trigger_out_gated(dut):
    """In AND mode, trigger_out (the trig_xbar drive) must pulse only on the
    TRUE local fire — i.e. when both the comparator and the pin held — never
    on the comparator alone (which by itself does not fire). Catch any
    trigger_out pulse across the pin-low window."""
    await _start_clk(dut)
    await _reset(dut)
    _always_match(dut)
    dut.ext_enable_in.value = 1
    dut.ext_and_in.value = 1
    dut.ext_trigger_in.value = 0

    await ClockCycles(dut.sample_clk, 20)
    await _pulse(dut.arm_pulse, dut, 1)

    # Watch trigger_out for 25 cycles while the pin is LOW — it must stay 0
    # (the comparator matches every cycle, but AND gate is closed).
    saw_pulse = 0
    for _ in range(25):
        await RisingEdge(dut.sample_clk)
        saw_pulse |= int(dut.trigger_out.value)
    assert saw_pulse == 0, (
        "trigger_out pulsed on comparator-only in AND mode (pin low) — would "
        "ping-pong coupled cores on a premature non-fire"
    )

    # Raise the pin: the true local fire should now pulse trigger_out once.
    dut.ext_trigger_in.value = 1
    fired_out = 0
    for _ in range(DERIVED_PIPE_STAGES + 2):
        await RisingEdge(dut.sample_clk)
        await ReadOnly()
        fired_out |= int(dut.trigger_out.value)
    assert int(dut.triggered.value) == 1, "AND fire (both high) expected"
    assert fired_out == 1, "true local fire should pulse trigger_out"
    dut._log.info("REA-REQ-413 PASS — AND-mode trigger_out gated correctly")


if __name__ == "__main__":
    main()
