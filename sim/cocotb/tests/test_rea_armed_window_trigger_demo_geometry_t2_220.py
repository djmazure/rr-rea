# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# RTL-T2.220, part 2 — the same discriminating experiment at the DEMO'S OWN
# GEOMETRY: depth 4096, pretrigger 1024, posttrigger 3071 (debug/u_dbg_rea.yml).
#
# Part 1 runs at depth 256 for speed and shows the trigger firing on a condition
# that becomes true mid-armed-window. That leaves one honest hole: "your model
# diverged from the bench". The window arithmetic is the plausible place for it
# — pretrig_valid_r/since_arm_r saturate at G_DEPTH, and a 1024-deep pre-trigger
# request is a different regime from a 127-deep one. So run the real numbers.
#
# A pass here says the elimination of mechanism (a) is not an artefact of a
# shrunken window. It does NOT say the bench is fine — see part 1's header.

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.triggers import ClockCycles

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402
from test_rea_armed_window_trigger_t2_220 import (  # noqa: E402
    ADDR_CTRL,
    ADDR_SOURCE,
    ADDR_STATUS,
    BIT_RUN_ENABLE,
    CTRL_BIT_ARM,
    STATUS_BIT_DONE,
    STATUS_BIT_TRIGGERED,
    _configure_run_enable_trigger,
    _drive_demo_probe,
    _jtag_read,
    _jtag_write,
    _poll_done,
    _reset,
    _start_clocks,
)

# The shipped demo's numbers, verbatim.
DEPTH = 4096
PRETRIG = 1024
POSTTRIG = 3071

GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": DEPTH,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": 4, "G_NUM_SOURCE": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_armed_window_trigger_demo_geometry_t2_220",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
                f"{_RTL_DIR}/rr_rea_crc_sweep.vhd",
                f"{_RTL_DIR}/rr_rea_fill_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_trust_core.vhd",
                f"{_RTL_DIR}/rr_rea_axis_window.vhd",
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


@cocotb.test()
@requires("REA-REQ-108")
async def test_demo_geometry_late_condition_still_fires(dut):
    """depth 4096 / pretrig 1024 / posttrig 3071 — the shipped configuration."""
    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_demo_probe(dut, 400_000))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    await _configure_run_enable_trigger(dut, PRETRIG, POSTTRIG)

    assert (int(dut.probe_i.value) >> BIT_RUN_ENABLE) & 1 == 0
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    # Armed, condition false: the pre-trigger window. Must not fire.
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert not (status & (STATUS_BIT_TRIGGERED | STATUS_BIT_DONE)), (
        f"fired with run_enable still 0 at demo geometry (STATUS=0x{status:02X})")

    await _jtag_write(dut, ADDR_SOURCE, 1)

    # posttrigger is 3071 stored samples, so give it the room it asks for.
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)
    done, status = await _poll_done(dut, 400)
    assert done, (
        "RTL-T2.220: at the DEMO'S OWN geometry the trigger did not fire on a "
        f"condition that became true while armed (STATUS=0x{status:02X}) — the "
        "depth-256 result was an artefact of the shrunken window")


if __name__ == "__main__":
    main()
