# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.20: the discriminator reference top
(rtl/rr_rea_dr_guard_discriminator_top.vhd) elaborates with its three rr_rea_intel
cores against the sld_virtual_jtag mock, leaves power-on reset, and drives a
live probe. A smoke test of the generic/port maps only. The three cores share
the mock's single TAP, so register reads through it are NOT meaningful here.
REA-REQ-968/969 are proven per core by the iface and wrapper tests."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402
from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import ClockCycles, ReadOnly  # noqa: E402

import rea_wrapper_generics as rwg  # noqa: E402
from engine.simulation import run_simulation  # noqa: E402

_TOP = (_Path(__file__).resolve().parents[3] / "rtl"
        / "rr_rea_dr_guard_discriminator_top.vhd")


@cocotb.test()
async def test_discriminator_top_elaborates_and_runs(dut):
    cocotb.start_soon(Clock(dut.clk_i, 20.0, unit="ns").start())
    await ClockCycles(dut.clk_i, 10)
    await ReadOnly()
    assert int(dut.rst_r.value) == 1, "power-on reset released too early"
    await ClockCycles(dut.clk_i, 300)
    await ReadOnly()
    assert int(dut.rst_r.value) == 0, "power-on reset never released"
    p0 = int(dut.probe_w.value)
    await ClockCycles(dut.clk_i, 1)
    await ReadOnly()
    assert int(dut.probe_w.value) != p0, "probe is not live"
    assert str(dut.trigger_o.value) in ("0", "1"), (
        f"trigger_o = {dut.trigger_o.value}, expected a resolved 0/1")


def main() -> None:
    libs = rwg.libraries()
    work = [f for f in libs["work"] if not f.endswith(("rr_rea_wrapper_harness.vhd",
                                                         "rr_rea_jtag_xilinx7.vhd",
                                                         "bscane2_mock.vhd"))]
    libs["work"] = work + [str(_TOP)]
    run_simulation(
        top_level="rr_rea_dr_guard_discriminator_top",
        module="test_rea_dr_guard_discriminator_top_p2_20",
        custom_libraries=libs,
        waves=False,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
