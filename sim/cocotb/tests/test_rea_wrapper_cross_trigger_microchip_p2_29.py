# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.29: rr_rea_jtag_microchip passes trigger_i to rr_rea_top and brings
rr_rea_top's trigger_pulse_o out (REA-REQ-975/976); REA-P2.32: it passes
G_ARM_AT_RESET and the G_RESET_* generics down to the core (REA-REQ-978).

The top level is rr_rea_jtag_microchip, the entity AGENTS.md tells a PolarFire
design to instantiate, so the check crosses both layers: the alias and
rr_rea_microchip (u_impl), down to the core (u_impl.u_top). The UJTAG
behavioural TAP (fixtures/ujtag_bfm.vhd) is elaborated but not scanned: these
checks look at the wrapper/core port boundary. Bodies in rea_wrapper_generics.py.
"""
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

GENERICS = {**rwg.GENERICS, "G_NUM_CHAN": 1, "G_NUM_SOURCE": 1, "G_CTRL_CHAIN": 1}
WHO = "rr_rea_jtag_microchip"


async def _start(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, 10, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.ext_trigger_i.value = 0
    dut.trigger_i.value = 0
    await ClockCycles(dut.sample_clk_i, 10)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 5)


@cocotb.test()
@requires("REA-REQ-975")
async def test_wrapper_trigger_i_reaches_core(dut):
    await _start(dut)
    await rwg.trigger_i_reaches_core(dut, dut.u_impl.u_top, dut.sample_clk_i, WHO)


@cocotb.test()
@requires("REA-REQ-976")
async def test_wrapper_trigger_pulse_o_reaches_pin(dut):
    await _start(dut)
    await rwg.trigger_pulse_o_reaches_pin(dut, dut.u_impl.u_top, dut.sample_clk_i, WHO)


# REA-P2.32: what rr_rea_jtag_microchip must hand rr_rea_microchip, read at
# the u_impl boundary. Hard-coded to rwg.GENERICS (G_ARM_AT_RESET reads as 1).
ALIAS_ARM_AT_RESET_GENERICS = (
    ("G_ARM_AT_RESET", 1),
    ("G_RESET_PRETRIG", 6),
    ("G_RESET_POSTTRIG", 7),
    ("G_RESET_TRIG_MODE", 1),
    ("G_RESET_TRIG_VALUE", 0x333),
    ("G_RESET_TRIG_MASK", 0xFFF),
    ("G_RESET_DECIM", 2),
    ("G_RESET_COND_CFG", rwg.RESET_COND_CFG_RAW),
    ("G_RESET_COND_VAL", 0x5A),
    ("G_RESET_QUAL_MODE", 2),
    ("G_RESET_QUAL_CFG", rwg.RESET_QUAL_CFG_RAW),
    ("G_RESET_QUAL_VAL", 0x7),
)


@cocotb.test()
@requires("REA-REQ-978")
async def test_alias_passes_arm_at_reset_generics_to_core(dut):
    """REA-P2.32: rr_rea_jtag_microchip passes G_ARM_AT_RESET and every
    G_RESET_* to rr_rea_microchip (u_impl). Read at that boundary, so it
    checks the alias's OWN generic map and nothing below it; the
    rr_rea_microchip map is test_rea_wrapper_generics_microchip_p3_6's."""
    await _start(dut)
    for name, want in ALIAS_ARM_AT_RESET_GENERICS:
        handle = getattr(dut.u_impl, name, None)
        got = int(handle.value) if handle is not None else None
        assert got == want, (
            f"REA-REQ-978: {WHO} does not pass {name} to rr_rea_microchip "
            f"(u_impl.{name} = {got!r}, want {want!r})")


@cocotb.test()
@requires("REA-REQ-977", "REA-REQ-978")
async def test_alias_arm_at_reset_config_reaches_core_regbank(dut):
    """REA-P2.32 end to end: under rr_rea_jtag_microchip the core's regbank
    holds every G_RESET_* value out of reset (alias map, then
    rr_rea_microchip's map, then rr_rea_top's)."""
    await _start(dut)
    rwg.check_arm_at_reset_regbank(dut.u_impl.u_top.u_regbank, WHO)


def main() -> None:
    rtl = rwg.RTL_DIR
    sources = [str(rwg.FIX / "rr_rea_build_id_stub.vhd")]
    sources += [str(rtl / f) for f in rwg.CORE_RTL]
    sources += [str(rwg.FIX / "ujtag_bfm.vhd"), str(rtl / "rr_rea_jtag_microchip.vhd")]
    run_simulation(
        top_level="rr_rea_jtag_microchip",
        module=_Path(__file__).stem,
        custom_libraries={"work": sources},
        generics=GENERICS,
        waves=True,
    )


if __name__ == "__main__":
    main()
