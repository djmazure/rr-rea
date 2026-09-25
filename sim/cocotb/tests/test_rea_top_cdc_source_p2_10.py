# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.10 / REA-REQ-961, sample -> register direction: the selftest busy
flag leaves the sample domain from a flop.

`selftest_busy_sclk` crosses to the register clock through u_cdc_st_busy. It
used to be the combinational OR `fill_busy or (sweep_busy and selftest_mode_r)`
of FSM state decodes, so logic sat in front of the synchronizer's first stage
(Vivado CDC-10 on the routed P2.10 build, the last one left) and a glitch could
be captured as a transient "not busy" mid-selftest. Registered, it equals that
OR delayed by exactly one sample_clk_i edge, on every cycle of a whole selftest
(fill, then the fill-triggered sweep). A combinational driver equals the OR in
the same cycle and fails this on the first busy edge.
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

from test_rea_fill_sweep_conflict_t1_2 import (  # noqa: E402
    ADDR_SELFTEST_CTRL,
    ADDR_SELFTEST_SEED,
    GENERICS,
    _jtag_write,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_top_cdc_source_p2_10",
        custom_libraries={
            "work": [
                f"{_RTL}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL}/rr_rea_dpram.vhd",
                f"{_RTL}/rr_rea_capture_fsm.vhd",
                f"{_RTL}/rr_rea_regbank.vhd",
                f"{_RTL}/rr_rea_cdc.vhd",
                f"{_RTL}/rr_rea_jtag_iface.vhd",
                f"{_RTL}/rr_rea_crc_sweep.vhd",
                f"{_RTL}/rr_rea_fill_fsm.vhd",
                f"{_RTL}/rr_rea_trust_core.vhd",
                f"{_RTL}/rr_rea_axis_window.vhd",
                f"{_RTL}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


def _busy_source(dut) -> int:
    return int(dut.fill_busy.value) | (int(dut.sweep_busy.value)
                                       & int(dut.selftest_mode_r.value))


@cocotb.test()
@requires("REA-REQ-961")
async def test_rea_req_961_selftest_busy_crosses_from_a_flop(dut):
    """Across a whole selftest (fill, then its sweep), `selftest_busy_sclk`
    on every sample cycle equals `fill_busy or (sweep_busy and
    selftest_mode_r)` of the PREVIOUS cycle — a flop, not the OR itself."""
    await _start_clocks(dut)
    await _reset(dut)
    await _jtag_write(dut, ADDR_SELFTEST_SEED, 0xCAFEBABE)
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 1)

    prev_src = None
    rose = fell = False
    mismatches = []
    for cycle in range(60000):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        src = _busy_source(dut)
        busy = int(dut.selftest_busy_sclk.value)
        if prev_src is not None and busy != prev_src:
            mismatches.append((cycle, prev_src, src, busy))
        rose |= busy == 1
        if rose and busy == 0 and src == 0:
            fell = True
            break
        prev_src = src
    assert rose, "selftest never went busy — the fill request did not start"
    assert fell, "selftest never finished inside the window"
    assert not mismatches, (
        f"{len(mismatches)} cycle(s) where selftest_busy_sclk is not the "
        f"one-edge-delayed busy source (cycle, src[t-1], src[t], busy[t]): "
        f"{mismatches[:6]} — combinational in front of u_cdc_st_busy "
        f"(CDC-10, REA-REQ-961)")


if __name__ == "__main__":
    main()
