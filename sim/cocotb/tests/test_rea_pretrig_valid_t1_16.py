# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# RTL-T1.16 — PRETRIG_VALID: how many pre-trigger cells belong to THIS capture.
#
# The sliding-window write enable stops at done_o so the captured window can be
# read back over JTAG (a 4096-word readback takes seconds; without the freeze
# the design would overwrite the very data being read). That gate is
# load-bearing and is exactly what the trust core's A1 proves — it must NOT be
# widened (see hint H.202).
#
# The consequence is that BETWEEN captures the buffer is frozen. If the trigger
# then fires within pretrig_len samples of the arm, the earliest pre-trigger
# cells still hold the PREVIOUS capture's tail. Those values are stale but
# entirely plausible, which is worse than uninitialised cells: a user reads them
# as the context that led to this trigger. Proven on silicon — capture B's
# pre-trigger window came back byte-for-byte equal to capture A's tail.
#
# The fix is honest accounting, not a wider write window: count samples actually
# stored since the arm, freeze that at the trigger, and let the host trim.

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

_SDK = _Path(__file__).resolve().parents[4] / "routertl"
if str(_SDK) not in _sys.path:
    _sys.path.insert(0, str(_SDK))

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": 4096}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
CLK_PERIOD_NS = 8


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_pretrig_valid_t1_16",
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
    await ClockCycles(dut.sample_clk_i, 4)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 2)


async def _pulse(sig, dut, n_cycles: int = 1):
    sig.value = 1
    for _ in range(n_cycles):
        await RisingEdge(dut.sample_clk_i)
    sig.value = 0


async def _arm(dut, pretrig: int, posttrig: int = 4):
    dut.pretrig_len_i.value = pretrig
    dut.posttrig_len_i.value = posttrig
    dut.trig_value_i.value = 0xABC
    dut.trig_mask_i.value = 0xFFF
    await _pulse(dut.arm_pulse_i, dut)


async def _fire_trigger(dut):
    """Present the trigger value for one cycle, then withdraw it."""
    dut.probe_i.value = 0xABC
    await RisingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0x000


@cocotb.test()
@requires("REA-REQ-107")
async def test_pretrig_valid_zero_when_trigger_fires_immediately(dut):
    """Trigger on the first sample after arm => NO pre-trigger context.

    This is the exact silicon repro: arm, immediate trigger. Every
    pre-trigger cell predates the arm, so none of them are context for this
    trigger and PRETRIG_VALID must say 0.
    """
    await _start_clk(dut)
    await _reset(dut)

    await _arm(dut, pretrig=64)
    await _fire_trigger(dut)
    await ClockCycles(dut.sample_clk_i, 20)

    valid = int(dut.pretrig_valid_o.value)
    assert valid <= 1, (
        f"PRETRIG_VALID={valid} after an immediate trigger. Those cells were "
        "written BEFORE the arm — they are the previous capture's tail, not "
        "context for this trigger. Reporting them as valid is what made the "
        "instrument lie (RTL-T1.16)."
    )


@cocotb.test()
@requires("REA-REQ-107")
async def test_pretrig_valid_counts_samples_stored_since_arm(dut):
    """Fire after N samples => exactly N pre-trigger cells are contiguous."""
    await _start_clk(dut)
    await _reset(dut)

    await _arm(dut, pretrig=64)
    n = 20
    await ClockCycles(dut.sample_clk_i, n)
    await _fire_trigger(dut)
    await ClockCycles(dut.sample_clk_i, 20)

    valid = int(dut.pretrig_valid_o.value)
    assert abs(valid - n) <= 2, (
        f"PRETRIG_VALID={valid}, expected ~{n} — it must track samples "
        "actually STORED since the arm, so the host knows how much of the "
        "pre-trigger window is real."
    )


@cocotb.test()
@requires("REA-REQ-107")
async def test_pretrig_valid_saturates_at_pretrig_len(dut):
    """Once a full pre-trigger window has been written, all of it is valid.

    The count is capped at pretrig_len: beyond that every pre-trigger cell is
    contiguous and the exact figure stops mattering. Without the cap the host
    would compute a negative trim.
    """
    await _start_clk(dut)
    await _reset(dut)

    pretrig = 16
    await _arm(dut, pretrig=pretrig)
    await ClockCycles(dut.sample_clk_i, pretrig * 4)
    await _fire_trigger(dut)
    await ClockCycles(dut.sample_clk_i, 20)

    valid = int(dut.pretrig_valid_o.value)
    assert valid == pretrig, (
        f"PRETRIG_VALID={valid}, expected exactly pretrig_len={pretrig}. "
        "Capping matters: an uncapped count makes the host trim by a negative "
        "amount and silently keep stale cells."
    )


@cocotb.test()
@requires("REA-REQ-107")
async def test_pretrig_valid_restarts_on_rearm(dut):
    """A second arm must NOT inherit the first capture's contiguity."""
    await _start_clk(dut)
    await _reset(dut)

    # First capture: plenty of context.
    await _arm(dut, pretrig=32)
    await ClockCycles(dut.sample_clk_i, 200)
    await _fire_trigger(dut)
    await ClockCycles(dut.sample_clk_i, 20)
    first = int(dut.pretrig_valid_o.value)
    assert first == 32, f"setup failed: expected a full window, got {first}"

    # Re-arm and trigger immediately — the classic back-to-back case.
    await _arm(dut, pretrig=32)
    await _fire_trigger(dut)
    await ClockCycles(dut.sample_clk_i, 20)
    second = int(dut.pretrig_valid_o.value)
    assert second <= 1, (
        f"PRETRIG_VALID={second} on the re-arm (first capture reported "
        f"{first}). Carrying the previous capture's figure forward is exactly "
        "the bug: capture B would present capture A's tail as its own "
        "pre-trigger context."
    )


if __name__ == "__main__":
    main()
