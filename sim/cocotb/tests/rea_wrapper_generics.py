# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""Shared body of the REA-P3.6 wrapper pass-through tests.

Each vendor wrapper is elaborated under a mocked TAP (fixtures/
rea_tap_mock_pkg + a BSCANE2 / sld_virtual_jtag mock) with the NON-default
generics G_TRIG_CONDS = 1, G_QUAL_CONDS = 1 and G_TRIG_STAGES = 2 (REA-P3.7),
and the register bus is scanned
THROUGH the wrapper. FEATURES is generic-derived inside rr_rea_top
(RTL-P3.1198), so it reads back the generics the core actually elaborated:
a wrapper that does not pass one on elaborates rr_rea_top's default and
reads 4 / 0 instead of 1 / 1. Not a test module itself (no test_ prefix).
"""
from __future__ import annotations

from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.handle import Force, Release
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
FIX = Path(__file__).resolve().parent / "fixtures"

GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": 64, "G_TIMESTAMP_W": 32,
    "G_TRIG_CONDS": 1, "G_QUAL_CONDS": 1,
    # REA-P3.7: a non-default sequencer depth, read back in FEATURES[30:28].
    "G_TRIG_STAGES": 2,
}

ADDR_CTRL, ADDR_STATUS, ADDR_POSTTRIG = 0x04, 0x08, 0x18
ADDR_TRIG_MODE, ADDR_FEATURES = 0x20, 0xD0
TRIG_MODE_EXT_EN, TRIG_MODE_EXT_AND = 1 << 3, 1 << 8
STATUS_ARMED, STATUS_TRIGGERED = 1 << 0, 1 << 1

CORE_RTL = [
    "rr_rea_pkg.vhd", "rr_rea_dpram.vhd", "rr_rea_capture_fsm.vhd",
    "rr_rea_regbank.vhd", "rr_rea_cdc.vhd", "rr_rea_jtag_iface.vhd",
    "rr_rea_crc_sweep.vhd", "rr_rea_fill_fsm.vhd", "rr_rea_trust_core.vhd",
    "rr_rea_trig_xbar.vhd", "rr_rea_axis_window.vhd", "rr_rea_top.vhd",
]


def libraries() -> dict[str, list[str]]:
    """Compile order: the mock-TAP package, the unisim component package,
    then the core, the two primitive mocks, both wrappers and the harness."""
    work = [str(FIX / "rr_rea_build_id_stub.vhd")]
    work += [str(RTL_DIR / f) for f in CORE_RTL]
    work += [
        str(FIX / "bscane2_mock.vhd"),
        str(FIX / "sld_virtual_jtag_mock.vhd"),
        str(RTL_DIR / "rr_rea_jtag_xilinx7.vhd"),
        str(RTL_DIR / "rr_rea_jtag_intel.vhd"),
        str(FIX / "rr_rea_wrapper_harness.vhd"),
    ]
    return {
        "rea_tap_mock": [str(FIX / "rea_tap_mock_pkg.vhd")],
        "unisim": [str(FIX / "unisim_vcomponents_mock.vhd")],
        "work": work,
    }


async def start(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, 25.0, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    for sig in (dut.tdi_i, dut.capture_i, dut.shift_en_i, dut.update_i,
                dut.sel_i, dut.ext_trigger_i):
        sig.value = 0
    dut.probe_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)


async def _phase(dut, capture=0, shift=0, update=0):
    dut.sel_i.value = 1
    dut.capture_i.value = capture
    dut.shift_en_i.value = shift
    dut.update_i.value = update
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0
    dut.update_i.value = 0


async def _shift_dr(dut, value: int, n_bits: int = 49) -> int:
    dut.sel_i.value = 1
    dut.shift_en_i.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi_i.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo_o.value) & 1) << i
        await RisingEdge(dut.tck_i)
    dut.shift_en_i.value = 0
    return out


def _frame(addr: int, data: int, write: bool) -> int:
    return (int(write) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFF_FFFF)


async def write(dut, addr: int, data: int):
    await _phase(dut, capture=1)
    await _shift_dr(dut, _frame(addr, data, True))
    await _phase(dut, update=1)
    dut.sel_i.value = 0


async def read(dut, addr: int) -> int:
    await _phase(dut, capture=1)
    await _shift_dr(dut, _frame(addr, 0, False))
    await _phase(dut, update=1)
    await ClockCycles(dut.tck_i, 2)
    await _phase(dut, capture=1)
    out = await _shift_dr(dut, 0)
    dut.sel_i.value = 0
    return out & 0xFFFF_FFFF


def check_features(features: int):
    assert features & 0xFF == 1, (
        f"FEATURES=0x{features:08X}: [7:0] (G_TRIG_CONDS) = {features & 0xFF}, "
        "expected 1 — the wrapper did not pass G_TRIG_CONDS to rr_rea_top")
    assert (features >> 22) & 1 == 1 and (features >> 24) & 0xF == 1, (
        f"FEATURES=0x{features:08X}: [22]/[27:24] (G_QUAL_CONDS) do not read "
        "1 — the wrapper did not pass G_QUAL_CONDS to rr_rea_top")
    assert (features >> 28) & 0x7 == 2, (
        f"FEATURES=0x{features:08X}: [30:28] (G_TRIG_STAGES) = "
        f"{(features >> 28) & 0x7}, expected 2 — the wrapper did not pass "
        "G_TRIG_STAGES to rr_rea_top (REA-P3.7)")
    assert (features >> 31) & 1 == 0 and (features >> 23) & 1 == 0, (
        f"FEATURES=0x{features:08X}: [31] (guarded DR, REA-P2.20) or [23] "
        "(reserved for sample liveness, REA-P1.1) reads 1 on a "
        "build that did not set G_DR_GUARD — the default must stay the frozen "
        "49-bit DR (REA-REQ-969)")


async def ext_trigger_fires_only_on_the_pin(dut):
    """TRIG_MODE ext_en|ext_and: fire ONLY when the pin is high (SPEC
    "External board-pin trigger"). Armed with the pin low, the core must not
    trigger; raising the pin must trigger it."""
    await write(dut, ADDR_POSTTRIG, 4)
    await write(dut, ADDR_TRIG_MODE, TRIG_MODE_EXT_EN | TRIG_MODE_EXT_AND)
    await write(dut, ADDR_CTRL, 1)              # arm toggle
    await ClockCycles(dut.sample_clk_i, 40)
    status = await read(dut, ADDR_STATUS)
    assert status & STATUS_ARMED, f"STATUS=0x{status:08X}: core never armed"
    assert not status & STATUS_TRIGGERED, (
        f"STATUS=0x{status:08X}: triggered with the external pin LOW")
    dut.ext_trigger_i.value = 1
    await ClockCycles(dut.sample_clk_i, 40)
    status = await read(dut, ADDR_STATUS)
    assert status & STATUS_TRIGGERED, (
        f"STATUS=0x{status:08X}: the external pin went high and the core did "
        "not trigger — ext_trigger_i does not reach rr_rea_top")


# ── REA-P2.29: cross-core trigger ports, checked at the wrapper/core boundary ─
#
# Both checks look at the rr_rea_top instance inside the wrapper (`core`), so
# they prove the WRAPPER passes the port and do not depend on what rr_rea_top
# does with it; test_rea_cross_trigger_top_p2_29 proves that end to end.


def harness_core(dut, vendor: str):
    """The rr_rea_top instance inside rr_rea_wrapper_harness's wrapper."""
    return getattr(dut, f"g_{vendor}").u_dut.u_top


def _bit(handle) -> int | None:
    return int(handle.value) if handle.value.is_resolvable else None


async def trigger_i_reaches_core(dut, core, clk, who: str):
    """The wrapper's trigger_i SHALL arrive on rr_rea_top's trigger_i
    (REA-REQ-975). Both levels are checked, so a constant tie-off fails."""
    req = "REA-REQ-975"
    for level in (1, 0, 1, 0):
        dut.trigger_i.value = level
        await RisingEdge(clk)
        await ReadOnly()
        seen = _bit(core.trigger_i)
        assert seen == level, (
            f"{req}: {who} trigger_i = {level} but rr_rea_top's trigger_i "
            f"reads {seen}: the wrapper does not pass trigger_i to the core")
        await RisingEdge(clk)


async def trigger_pulse_o_reaches_pin(dut, core, clk, who: str):
    """rr_rea_top's trigger_pulse_o SHALL arrive on the wrapper's
    trigger_pulse_o (REA-REQ-976). The core's port is forced to each level in
    turn; a wrapper that ties its output off reads the tie-off instead."""
    req = "REA-REQ-976"
    try:
        for level in (1, 0, 1, 0):
            core.trigger_pulse_o.value = Force(level)
            await RisingEdge(clk)
            await ReadOnly()
            seen = _bit(dut.trigger_pulse_o)
            assert seen == level, (
                f"{req}: rr_rea_top's trigger_pulse_o = {level} but {who} "
                f"trigger_pulse_o reads {seen}: the wrapper does not bring "
                "the core's trigger_pulse_o out")
            await RisingEdge(clk)
    finally:
        core.trigger_pulse_o.value = Release()
