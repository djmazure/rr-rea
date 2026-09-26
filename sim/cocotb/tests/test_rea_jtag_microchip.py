# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_jtag_microchip — Microchip PolarFire UJTAG wrapper (RTL-P3.1706).
#
# Moved from routertl (src/units + sim/cocotb/tests/units) into the package
# under REA-P2.13, so a consumer that installs routertl/rea gets the PolarFire
# wrapper too. The UJTAG hard macro is replaced in sim by
# fixtures/ujtag_bfm.vhd, a behavioural TAP model; the on-silicon witness is
# routertl examples/polarfire_soc_rea_demo (RTL-P2.1279).

import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402
from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import ClockCycles, Timer  # noqa: E402

from engine.simulation import run_simulation  # noqa: E402

_RTL_DIR = _Path(__file__).resolve().parents[3] / "rtl"
_FIX = _Path(__file__).resolve().parent / "fixtures"

GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": 64,
    "G_TIMESTAMP_W": 32,
    "G_NUM_CHAN": 1,
    "G_NUM_SOURCE": 1,
    "G_CTRL_CHAIN": 1,
}

OPCODE_USER1 = 0x55
OPCODE_USER2 = 0x56

ADDR_SAMPLE_W = 0x000C
ADDR_DEPTH = 0x0010
ADDR_SOURCE = 0x003C
ADDR_TIMESTAMP_W = 0x00C4


async def tck_cycle(dut, tms: int, tdi: int = 0) -> int:
    """Drive TMS/TDI, clock TCK low then high, sampling TDO while clock is low."""
    dut.jtag_tms_i.value = tms
    dut.jtag_tdi_i.value = tdi
    dut.jtag_tck_i.value = 0
    await Timer(10, unit="ns")
    tdo = int(dut.jtag_tdo_o.value)
    dut.jtag_tck_i.value = 1
    await Timer(10, unit="ns")
    return tdo


async def tap_reset(dut):
    """Reset JTAG TAP via TRSTB and navigate to Run-Test-Idle."""
    dut.jtag_trstb_i.value = 0
    dut.jtag_tms_i.value = 1
    dut.jtag_tdi_i.value = 0
    for _ in range(5):
        dut.jtag_tck_i.value = 0
        await Timer(10, unit="ns")
        dut.jtag_tck_i.value = 1
        await Timer(10, unit="ns")
    dut.jtag_trstb_i.value = 1
    # Move from Test-Logic-Reset (TMS=0) to Run-Test-Idle
    await tck_cycle(dut, tms=0)


async def ir_scan(dut, opcode: int, width: int = 8):
    """Load instruction register via Shift-IR and return to Run-Test-Idle."""
    # From Run-Test-Idle: Select-DR-Scan -> Select-IR-Scan -> Capture-IR -> Shift-IR
    await tck_cycle(dut, tms=1)
    await tck_cycle(dut, tms=1)
    await tck_cycle(dut, tms=0)
    await tck_cycle(dut, tms=0)
    for i in range(width):
        bit = (opcode >> i) & 1
        tms = 1 if i == width - 1 else 0
        await tck_cycle(dut, tms=tms, tdi=bit)
    # Exit1-IR -> Update-IR -> Run-Test-Idle
    await tck_cycle(dut, tms=1)
    await tck_cycle(dut, tms=0)


async def dr_scan(dut, value: int, width: int = 49) -> int:
    """Scan 49-bit DR frame LSB-first and return the shifted-out value."""
    # From Run-Test-Idle: Select-DR-Scan -> Capture-DR -> Shift-DR
    await tck_cycle(dut, tms=1)
    await tck_cycle(dut, tms=0)
    await tck_cycle(dut, tms=0)
    out_val = 0
    for i in range(width):
        bit = (value >> i) & 1
        tms = 1 if i == width - 1 else 0
        tdo = await tck_cycle(dut, tms=tms, tdi=bit)
        if tdo:
            out_val |= (1 << i)
    # Exit1-DR -> Update-DR -> Run-Test-Idle
    await tck_cycle(dut, tms=1)
    await tck_cycle(dut, tms=0)
    return out_val


async def write_reg(dut, addr: int, data: int):
    """Write 32-bit register over 49-bit DR frame with RTI settling cycles."""
    frame = (1 << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)
    await dr_scan(dut, frame, 49)
    for _ in range(5):
        await tck_cycle(dut, tms=0)


async def read_reg(dut, addr: int) -> int:
    """Read 32-bit register over two 49-bit DR scans with idle cycles."""
    read_cmd = ((addr & 0xFFFF) << 32)
    await dr_scan(dut, read_cmd, 49)
    for _ in range(16):
        await tck_cycle(dut, tms=0)
    readback = await dr_scan(dut, 0, 49)
    return readback & 0xFFFFFFFF


@cocotb.test()
async def test_microchip_ujtag_tap_reset_and_read_geometry(dut):
    """Verify UJTAG TAP reset and register readback for SAMPLE_W, DEPTH, and TIMESTAMP_W."""
    cocotb.start_soon(Clock(dut.sample_clk_i, 10, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.ext_trigger_i.value = 0
    await ClockCycles(dut.sample_clk_i, 10)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 5)

    await tap_reset(dut)
    dut._log.info("TAP reset completed.")

    # Select UJTAG user opcode 0x55 (chain 1)
    await ir_scan(dut, OPCODE_USER1)
    dut._log.info(f"Loaded IR opcode 0x{OPCODE_USER1:02X}.")

    # Read SAMPLE_W (0x0C)
    sample_w = await read_reg(dut, ADDR_SAMPLE_W)
    dut._log.info(f"Read SAMPLE_W = {sample_w} (expected {GENERICS['G_SAMPLE_W']})")
    assert sample_w == GENERICS["G_SAMPLE_W"], f"SAMPLE_W mismatch: got {sample_w}, expected {GENERICS['G_SAMPLE_W']}"

    # Read DEPTH (0x10)
    depth = await read_reg(dut, ADDR_DEPTH)
    dut._log.info(f"Read DEPTH = {depth} (expected {GENERICS['G_DEPTH']})")
    assert depth == GENERICS["G_DEPTH"], f"DEPTH mismatch: got {depth}, expected {GENERICS['G_DEPTH']}"

    # Read TIMESTAMP_W (0xC4)
    ts_w = await read_reg(dut, ADDR_TIMESTAMP_W)
    dut._log.info(f"Read TIMESTAMP_W = {ts_w} (expected {GENERICS['G_TIMESTAMP_W']})")
    assert ts_w == GENERICS["G_TIMESTAMP_W"], f"TIMESTAMP_W mismatch: got {ts_w}, expected {GENERICS['G_TIMESTAMP_W']}"


@cocotb.test()
async def test_microchip_ujtag_source_write_and_isolation(dut):
    """Verify write-side SOURCE register driving source_o pins and opcode isolation."""
    cocotb.start_soon(Clock(dut.sample_clk_i, 10, unit="ns").start())
    dut.sample_rst_i.value = 0
    dut.probe_i.value = 0
    dut.ext_trigger_i.value = 0

    await tap_reset(dut)
    await ir_scan(dut, OPCODE_USER1)

    # Initially source_o should be 0
    await ClockCycles(dut.sample_clk_i, 5)
    assert int(dut.source_o.value) == 0, "source_o not initially 0"

    # Write 1 to SOURCE register (0x3C)
    await write_reg(dut, ADDR_SOURCE, 1)
    await ClockCycles(dut.sample_clk_i, 10)
    dut._log.info(f"Written 1 to SOURCE, dut.source_o.value = {int(dut.source_o.value)}")
    assert int(dut.source_o.value) == 1, f"source_o expected 1, got {int(dut.source_o.value)}"

    # Write 0 to SOURCE register
    await write_reg(dut, ADDR_SOURCE, 0)
    await ClockCycles(dut.sample_clk_i, 10)
    dut._log.info(f"Written 0 to SOURCE, dut.source_o.value = {int(dut.source_o.value)}")
    assert int(dut.source_o.value) == 0, f"source_o expected 0, got {int(dut.source_o.value)}"

    # Isolation check: switch to OPCODE_USER2 (0x56) — core configured for chain 1 (0x55)
    await ir_scan(dut, OPCODE_USER2)
    dut._log.info(f"Loaded non-matching opcode 0x{OPCODE_USER2:02X} — testing isolation.")

    # Attempt to write 1 while unselected
    await write_reg(dut, ADDR_SOURCE, 1)
    await ClockCycles(dut.sample_clk_i, 10)
    dut._log.info(f"Unselected write attempt, dut.source_o.value = {int(dut.source_o.value)}")
    assert int(dut.source_o.value) == 0, "source_o modified while core was unselected (isolation failed!)"

    # Re-select with OPCODE_USER1
    await ir_scan(dut, OPCODE_USER1)
    await write_reg(dut, ADDR_SOURCE, 1)
    await ClockCycles(dut.sample_clk_i, 10)
    assert int(dut.source_o.value) == 1, "source_o failed to update after re-selecting matching opcode"


def main():
    """Run simulation for rr_rea_microchip"""
    rtl = [
        "rr_rea_pkg.vhd", "rr_rea_fill_fsm.vhd", "rr_rea_cdc.vhd",
        "rr_rea_dpram.vhd", "rr_rea_crc_sweep.vhd", "rr_rea_trust_core.vhd",
        "rr_rea_trig_xbar.vhd", "rr_rea_capture_fsm.vhd",
        "rr_rea_regbank.vhd", "rr_rea_jtag_iface.vhd",
        "rr_rea_axis_window.vhd", "rr_rea_top.vhd",
    ]
    sources = [str(_FIX / "rr_rea_build_id_stub.vhd")]
    sources += [str(_RTL_DIR / f) for f in rtl]
    sources += [
        str(_FIX / "ujtag_bfm.vhd"),
        str(_RTL_DIR / "rr_rea_jtag_microchip.vhd"),
    ]
    run_simulation(
        top_level="rr_rea_microchip",
        module="test_rea_jtag_microchip",
        custom_libraries={"work": sources},
        generics=GENERICS,
        waves=True,
    )


if __name__ == "__main__":
    main()
