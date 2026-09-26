# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.6: rr_rea_microchip passes G_TRIG_CONDS, G_QUAL_CONDS and (REA-P3.7) G_TRIG_STAGES to
rr_rea_top (it passed neither before 1.8.0, so util_field / the lean profile
could not be built on PolarFire through the wrapper), and ext_trigger_i
reaches the core. Scanned through the UJTAG behavioural TAP
(fixtures/ujtag_bfm.vhd) with the same helpers as test_rea_jtag_microchip."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402
from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import ClockCycles  # noqa: E402

import rea_wrapper_generics as rwg  # noqa: E402
from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402
from test_rea_jtag_microchip import (  # noqa: E402
    OPCODE_USER1, ir_scan, read_reg, tap_reset, write_reg,
)

GENERICS = {**rwg.GENERICS, "G_NUM_CHAN": 1, "G_NUM_SOURCE": 1, "G_CTRL_CHAIN": 1}


async def _start(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, 10, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.ext_trigger_i.value = 0
    await ClockCycles(dut.sample_clk_i, 10)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 5)
    await tap_reset(dut)
    await ir_scan(dut, OPCODE_USER1)


@cocotb.test()
@requires("REA-REQ-966", "REA-REQ-967")
async def test_wrapper_passes_trig_and_qual_conds(dut):
    await _start(dut)
    rwg.check_features(await read_reg(dut, rwg.ADDR_FEATURES))


@cocotb.test()
async def test_wrapper_ext_trigger_reaches_core(dut):
    """TRIG_MODE ext_en|ext_and: armed with the pin low the core must not
    trigger; raising the pin must trigger it."""
    await _start(dut)
    await write_reg(dut, rwg.ADDR_POSTTRIG, 4)
    await write_reg(dut, rwg.ADDR_TRIG_MODE,
                    rwg.TRIG_MODE_EXT_EN | rwg.TRIG_MODE_EXT_AND)
    await write_reg(dut, 0x04, 1)                      # CTRL arm toggle
    await ClockCycles(dut.sample_clk_i, 40)
    status = await read_reg(dut, rwg.ADDR_STATUS)
    assert status & rwg.STATUS_ARMED, f"STATUS=0x{status:08X}: never armed"
    assert not status & rwg.STATUS_TRIGGERED, (
        f"STATUS=0x{status:08X}: triggered with the external pin LOW")
    dut.ext_trigger_i.value = 1
    await ClockCycles(dut.sample_clk_i, 40)
    status = await read_reg(dut, rwg.ADDR_STATUS)
    assert status & rwg.STATUS_TRIGGERED, (
        f"STATUS=0x{status:08X}: the pin went high and the core did not "
        "trigger — ext_trigger_i does not reach rr_rea_top")


def main() -> None:
    rtl = rwg.RTL_DIR
    sources = [str(rwg.FIX / "rr_rea_build_id_stub.vhd")]
    sources += [str(rtl / f) for f in rwg.CORE_RTL]
    sources += [str(rwg.FIX / "ujtag_bfm.vhd"), str(rtl / "rr_rea_jtag_microchip.vhd")]
    run_simulation(
        top_level="rr_rea_microchip",
        module="test_rea_wrapper_generics_microchip_p3_6",
        custom_libraries={"work": sources},
        generics=GENERICS,
        waves=True,
    )


if __name__ == "__main__":
    main()
