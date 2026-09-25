# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.6 — a window the trigger latency would overrun is OVERFLOW; every
legal window reads back intact (REA-REQ-107, REA-REQ-964).

The samples stored while the trigger pipeline catches up (C_PIPE_STAGES,
reported as TRIG_LATENCY) land past the trigger cell whatever POSTTRIG says,
so a capture really spans PRETRIG + max(POSTTRIG, lag) + 1 ring cells. When
that exceeds DEPTH the ring overwrote the oldest pre-trigger cells (measured:
59 genuine of PRETRIG 62, DEPTH 64). The contract is now: OVERFLOW asserts at
arm exactly when PRETRIG + max(POSTTRIG, TRIG_LATENCY) >= DEPTH, and every
window below that reads back intact with an exact PRETRIG_VALID.

Bench and oracle are REA-T2.5's (counter probe, every DPRAM write recorded,
full-window cell-by-cell check). Lag here: G_SAMPLE_W 16 -> 2 slices, 4
trigger conditions -> 2 reduce stages, TRIG_LATENCY 4.

Run via:  rr sim run test_rea_window_equals_depth_t2_6
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from test_rea_pretrig_valid_sat_t2_5 import (  # noqa: E402
    DEPTH,
    GENERICS,
    INSTANCES,
    PIPE_STAGES,
    _capture,
    _judge,
    _start,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL_DIR = _Path(__file__).resolve().parents[3] / "rtl"
_FIX = _Path(__file__).resolve().parent / "fixtures"
STATUS_OVERFLOW = 1 << 3


def main() -> None:
    run_simulation(
        top_level="rr_rea_qual_lockstep_harness",
        module="test_rea_window_equals_depth_t2_6",
        custom_libraries={
            "work": [
                str(_RTL_DIR / "rr_rea_pkg.vhd"),
                str(_FIX / "rr_rea_capture_fsm_ref_v09.vhd"),
                str(_RTL_DIR / "rr_rea_capture_fsm.vhd"),
                str(_FIX / "rr_rea_qual_lockstep_harness.vhd"),
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


async def _overflow_after_arm(dut, pretrig: int, posttrig: int) -> dict:
    dut.pretrig_len_i.value = pretrig
    dut.posttrig_len_i.value = posttrig
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0
    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    got = {name: bool(int(getattr(dut, status).value) & STATUS_OVERFLOW)
           for name, (*_w, status) in INSTANCES.items()}
    await RisingEdge(dut.sample_clk_i)
    dut.reset_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.reset_pulse_i.value = 0
    return got


@cocotb.test()
@requires("REA-REQ-107")
async def test_rea_t2_6_overflow_covers_the_trigger_latency(dut):
    """OVERFLOW asserts exactly when PRETRIG + max(POSTTRIG, TRIG_LATENCY)
    >= DEPTH, on every instance."""
    await _start(dut)
    cases = [
        (DEPTH - 2, 1, True),                       # 62 + max(1, 4) = 66
        (DEPTH - PIPE_STAGES, 0, True),             # 60 + 4 = 64
        (DEPTH - 1 - PIPE_STAGES, 0, False),        # 59 + 4 = 63
        (DEPTH // 2, DEPTH // 2 - 1, False),        # rr ila default shape
        (DEPTH // 2, DEPTH // 2, True),             # old rule's first overflow
    ]
    failures = []
    for pre, post, want in cases:
        got = await _overflow_after_arm(dut, pre, post)
        for name, flag in got.items():
            if flag != want:
                failures.append(f"u_{name} PRETRIG={pre} POSTTRIG={post}: "
                                f"OVERFLOW={int(flag)}, want {int(want)}")
    assert not failures, "\n  ".join(failures)


@cocotb.test()
@requires("REA-REQ-107", "REA-REQ-963")
async def test_rea_t2_6_largest_legal_windows_read_back_intact(dut):
    """At the legal boundary (PRETRIG + max(POSTTRIG, lag) = DEPTH - 1),
    DECIM 0..3 at every phase: every cell of the window is this capture's and
    PRETRIG_VALID = PRETRIG."""
    bench = await _start(dut)
    failures = []
    for pre, post in ((DEPTH - 1 - PIPE_STAGES, 0),
                      (DEPTH // 2, DEPTH // 2 - 1)):
        for decim in range(0, 4):
            for phase in range(decim + 1):
                cap = await _capture(bench, decim,
                                     4 * DEPTH * (decim + 1) + phase,
                                     pretrig=pre, posttrig=post)
                for name in INSTANCES:
                    for err in _judge(bench, name, decim, cap, full_window=True):
                        failures.append(f"u_{name} PRETRIG={pre} POSTTRIG={post} "
                                        f"DECIM={decim} phase={phase}: {err}")
    assert not failures, (f"{len(failures)} defect(s):\n  "
                          + "\n  ".join(failures[:24]))


if __name__ == "__main__":
    main()
