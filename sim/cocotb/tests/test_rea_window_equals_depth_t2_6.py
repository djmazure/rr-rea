# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.6 — a window of exactly DEPTH cells (PRETRIG + POSTTRIG = DEPTH - 1,
legal per SPEC: overflow only when PRETRIG + POSTTRIG >= DEPTH) reads back
intact.

Measured failing: the FSM stores one sample past the last window cell before
the write enable freezes. In a shorter window that store lands in an unused
cell; at window == DEPTH it lands on start_ptr and the oldest pre-trigger
cell reads back as the sample one ring later. Bench and oracle are REA-T2.5's
(counter probe, every DPRAM write recorded).

expect_fail until REA-T2.6 is fixed — remove the marker in the fixing commit.

Run via:  rr sim run test_rea_window_equals_depth_t2_6
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from test_rea_pretrig_valid_sat_t2_5 import (  # noqa: E402
    DEPTH,
    GENERICS,
    INSTANCES,
    _capture,
    _judge,
    _start,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL_DIR = _Path(__file__).resolve().parents[3] / "rtl"
_FIX = _Path(__file__).resolve().parent / "fixtures"


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


# REA-T2.6: open defect — the extra store clobbers cell 0 at window == DEPTH.
@cocotb.test(expect_fail=True)
@requires("REA-REQ-501")
async def test_rea_t2_6_window_of_depth_cells_reads_back_intact(dut):
    """PRETRIG = DEPTH-2, POSTTRIG = 1 (window = DEPTH), DECIM 0..3 at every
    trigger phase: every cell of the read-back window is this capture\'s."""
    bench = await _start(dut)
    failures = []
    for decim in range(0, 4):
        for phase in range(decim + 1):
            cap = await _capture(bench, decim, 4 * DEPTH * (decim + 1) + phase,
                                 pretrig=DEPTH - 2, posttrig=1)
            for name in INSTANCES:
                for err in _judge(bench, name, decim, cap, full_window=True):
                    failures.append(
                        f"u_{name} DECIM={decim} phase={phase}: {err}")
    assert not failures, (f"{len(failures)} defect(s):\n  "
                          + "\n  ".join(failures[:24]))


if __name__ == "__main__":
    main()
