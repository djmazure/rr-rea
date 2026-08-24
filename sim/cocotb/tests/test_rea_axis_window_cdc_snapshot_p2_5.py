# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# REA-P2.5 revision — the AXIS window engine SHALL snapshot the SETTLED
# start_ptr, never a stale/torn one racing across the done crossing
# (REA-REQ-915; the P2.5 landing-refusal finding #1).
#
# WHY. In rr_rea_top the capture FSM writes done_r and start_ptr_r on the SAME
# sample-clock edge (REA-REQ-104), and the two cross to the register-bus domain
# on SEPARATE rr_rea_sync_word instances — a 1-bit `done` and a multi-bit
# `start_ptr`, no gray code. Two independent 2-flop synchronizers observing the
# same source transition can present their new outputs up to one dest cycle
# apart, so at the dest edge `done` first resolves high, `start_ptr` may still
# read the STALE pre-capture value. If the engine latches start_ptr on that
# edge it rotates the WHOLE burst to the wrong window, while the later DATA_BASE
# walk reads the settled register — exactly the silent-on-silicon divergence
# the lander reproduced with a 3 ns skew model. nvc gives both synchronizers
# identical delay, so a plain full-core sim can NEVER see it.
#
# This test drives the engine's snapshot boundary directly through
# rr_rea_axis_window_cdc_harness and MODELS the worst-case divergence: `done`
# rises while start_ptr_i still holds the stale value, and start_ptr_i only
# settles to the true window pointer SKEW cycles later. The harness backs port B
# with data == address, so every beat reveals the physical ring address the
# engine walked. The engine must emit the burst rotated on the SETTLED
# start_ptr.
#
# FALSIFIABILITY. Against the pre-fix engine (snapshot on the done edge) this is
# RED: the beat sequence starts at the stale pointer. Against the fixed engine
# (wait for start_ptr to settle) it is GREEN. Proven red-before-green on the
# unfixed tree during the P2.5 revision.

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

_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# One 32-bit word per sample cell (12 <= 32) and no timestamp plane, so one beat
# == one physical ring address — the cleanest lens on which start_ptr the engine
# snapshotted.
GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": 32, "G_TIMESTAMP_W": 0}
DEPTH = GENERICS["G_DEPTH"]

CAPTURE_LEN = 8
# The stale (pre-capture) start_ptr the crossing may still present at the done
# edge, vs the settled window pointer. SETTLED wraps the ring (28..31, 0..3) so a
# stale-vs-settled mix-up is unmistakable in the beat sequence.
STALE_PTR = 0
SETTLED_PTR = 28
# How many dest cycles start_ptr lags `done`. >=1 breaks a snapshot-on-done
# engine; the fixed engine's settle wait must exceed this.
SKEW_CYCLES = 2

CLK_PERIOD_NS = 10.0


def main() -> None:
    run_simulation(
        top_level="rr_rea_axis_window_cdc_harness",
        module="test_rea_axis_window_cdc_snapshot_p2_5",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_axis_window.vhd",
                f"{_FIX}/rr_rea_axis_window_cdc_harness.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


async def _collect_burst(dut) -> tuple[list[int], int]:
    """Drain the AXIS burst with tready held high; return (beats, tlast_index)."""
    beats: list[int] = []
    last_index = -1
    dut.m_tready_i.value = 1
    for _ in range(4000):
        await ReadOnly()
        if int(dut.m_tvalid_o.value) and int(dut.m_tready_i.value):
            beats.append(int(dut.m_tdata_o.value))
            if int(dut.m_tlast_o.value):
                last_index = len(beats) - 1
                await RisingEdge(dut.clk_i)
                break
        await RisingEdge(dut.clk_i)
    return beats, last_index


@cocotb.test()
@requires("REA-REQ-915", "REA-REQ-912")
async def test_engine_snapshots_settled_start_ptr(dut):
    cocotb.start_soon(Clock(dut.clk_i, CLK_PERIOD_NS, unit="ns").start())

    # Reset; present the STALE start_ptr, as the crossing would at the done edge.
    dut.rst_i.value = 1
    dut.done_i.value = 0
    dut.start_ptr_i.value = STALE_PTR
    dut.capture_len_i.value = CAPTURE_LEN
    dut.m_tready_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    dut.rst_i.value = 0
    await ClockCycles(dut.clk_i, 2)

    # Raise done with start_ptr STILL stale — a snapshot-on-done engine latches
    # STALE_PTR here. Then let start_ptr settle SKEW cycles later, as the slower
    # of the two independent synchronizers would.
    dut.done_i.value = 1
    await ClockCycles(dut.clk_i, SKEW_CYCLES)
    dut.start_ptr_i.value = SETTLED_PTR

    beats, last_index = await _collect_burst(dut)

    expected = [(SETTLED_PTR + i) % DEPTH for i in range(CAPTURE_LEN)]
    assert beats == expected, (
        "AXIS burst rotated on the STALE start_ptr, not the settled window "
        f"pointer (CDC race, REA-REQ-915):\n  beats   ={beats}\n  expected={expected}\n"
        f"  (a burst starting at {STALE_PTR} means the engine snapshotted "
        f"start_ptr on the done edge before it settled to {SETTLED_PTR})"
    )
    assert len(beats) == CAPTURE_LEN, f"got {len(beats)} beats, want {CAPTURE_LEN}"
    assert last_index == CAPTURE_LEN - 1, (
        f"tlast on beat {last_index}, want the final beat {CAPTURE_LEN - 1}"
    )

    dut._log.info(
        f"REA-P2.5 CDC PASS — engine snapshotted the SETTLED start_ptr "
        f"({SETTLED_PTR}) despite a {SKEW_CYCLES}-cycle done→start_ptr skew; "
        f"burst walked {beats}"
    )


if __name__ == "__main__":
    main()
