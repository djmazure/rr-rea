# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_top — write-side SOURCE integration test (RTL-P2.837,
# REA-REQ-700/701/702).
#
# Exercises the ISSP-style write-side "source": a JTAG write to the
# SOURCE register (0x3C) drives control bit(s) INTO the design, crossed
# jtag_clk -> sample_clk by rr_rea_sync_word, and presented on
# rr_rea_top's `source_out` port. Mirrors what the System Console / xsdb
# host does over JTAG (a plain register write), the same way the other
# REA tests emulate the host wire protocol directly.
#
# The contract this pins:
#   REA-REQ-700  safe default — source_out powers up all-zeros and stays
#                gated across config loads, arm, and reset pulses (no
#                auto-release: the gating is the whole point of P2.837).
#   REA-REQ-701  a JTAG write releases the gated signal in the sample_clk
#                domain via the two-flop synchronizer; a clearing write
#                re-gates it; the register round-trips on JTAG readback.
#   REA-REQ-702  only the low G_NUM_SOURCE bits reach source_out — upper
#                SOURCE bits are stored (readback) but never drive the port.

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

# 4 source bits so we can prove multi-bit fan-out and upper-bit isolation.
G_NUM_SOURCE = 4
GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": 64,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
    "G_NUM_SOURCE": G_NUM_SOURCE,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# Register addresses (frozen contract — see rr_rea_pkg.vhd)
ADDR_CTRL       = 0x04
ADDR_PRETRIG    = 0x14
ADDR_POSTTRIG   = 0x18
ADDR_TRIG_MODE  = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK  = 0x28
ADDR_SOURCE     = 0x3C

CTRL_BIT_ARM   = 0x01
CTRL_BIT_RESET = 0x02


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_source",
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
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Helpers (JTAG protocol — same wire format as test_rea_top.py) ────

SAMPLE_PERIOD_NS = 8.0    # 125 MHz sample clock
TCK_PERIOD_NS    = 25.0   # 40 MHz JTAG clock
# Two-flop synchronizer settle margin, in sample_clk cycles.
CDC_SETTLE = 6


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    dut.probe_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)


async def _capture_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 1
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.update_i.value = 0
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


async def _update_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 1
    await RisingEdge(dut.tck_i)
    dut.update_i.value = 0
    dut.sel_i.value = 0


def _frame(addr: int, data: int, write: bool) -> int:
    return ((1 if write else 0) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)


async def _jtag_write(dut, addr: int, data: int):
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, data, write=True), 49)
    await _update_phase(dut)


async def _jtag_read(dut, addr: int) -> int:
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)
    await ClockCycles(dut.tck_i, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


async def _source_out(dut) -> int:
    """Read source_out (sample_clk domain) after letting the 2-flop sync
    settle."""
    await ClockCycles(dut.sample_clk_i, CDC_SETTLE)
    await ReadOnly()
    val = int(dut.source_o.value)
    await NextTimeStep()
    return val


# ── REA-REQ-700: safe default + no auto-release ──────────────────────


@cocotb.test()
@requires("REA-REQ-700")
async def test_rea_req_700_source_safe_default(dut):
    """source_out powers up all-zeros (gated/safe) and stays that way
    across config loads, arm, and reset — nothing but an explicit SOURCE
    write releases it. This is the entire point of P2.837: remove the
    arm-vs-autofire race by starting GATED."""
    await _start_clocks(dut)
    await _reset(dut)

    # Right out of reset: every source bit must be low (safe/inactive).
    assert await _source_out(dut) == 0, (
        "REA-REQ-700 failed: source_out non-zero straight out of reset — "
        "the gated DUT signal must start SAFE, not released"
    )

    # Load a full trigger config + arm + reset pulse. NONE of this is a
    # SOURCE write, so source_out must not so much as flicker: no auto-release
    # on config load / arm (the trap this feature exists to kill).
    await _jtag_write(dut, ADDR_PRETRIG,    16)
    await _jtag_write(dut, ADDR_POSTTRIG,   16)
    await _jtag_write(dut, ADDR_TRIG_MODE,  0x0000_0001)  # value_match
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0x0000_0042)
    await _jtag_write(dut, ADDR_TRIG_MASK,  0x0000_0FFF)
    await _jtag_write(dut, ADDR_CTRL,       CTRL_BIT_ARM)
    await _jtag_write(dut, ADDR_CTRL,       CTRL_BIT_RESET)

    assert await _source_out(dut) == 0, (
        "REA-REQ-700 failed: source_out changed after config/arm/reset with "
        "no SOURCE write — auto-release bug, the gate leaked open"
    )
    dut._log.info("REA-REQ-700 PASS — source_out gated-safe by default")


# ── REA-REQ-701: write releases/re-gates via the CDC ─────────────────


@cocotb.test()
@requires("REA-REQ-701")
async def test_rea_req_701_source_release_regate(dut):
    """A JTAG write to SOURCE drives source_out in the sample_clk domain
    (release); a clearing write re-gates it; the register round-trips on
    readback. Proves the jtag_clk -> sample_clk crossing via
    rr_rea_sync_word actually lands the value at the DUT-facing port."""
    await _start_clocks(dut)
    await _reset(dut)

    # Release bits 0 and 2 (0b0101). After the 2-flop sync settles the
    # sample-domain port must read exactly that.
    await _jtag_write(dut, ADDR_SOURCE, 0x5)
    assert await _source_out(dut) == 0x5, (
        f"REA-REQ-701 failed: after SOURCE<=0x5, source_out="
        f"0x{int(dut.source_o.value):X}, expected 0x5 (CDC didn't land)"
    )
    # Register round-trips on JTAG readback.
    rb = await _jtag_read(dut, ADDR_SOURCE)
    assert rb == 0x5, (
        f"REA-REQ-701 failed: SOURCE readback 0x{rb:X}, expected 0x5"
    )

    # Re-gate: clear the register, the port must drop back to all-zeros.
    await _jtag_write(dut, ADDR_SOURCE, 0x0)
    assert await _source_out(dut) == 0x0, (
        f"REA-REQ-701 failed: after SOURCE<=0, source_out="
        f"0x{int(dut.source_o.value):X}, expected 0x0 (failed to re-gate)"
    )

    # Full-width release (all G_NUM_SOURCE bits) then clear again.
    await _jtag_write(dut, ADDR_SOURCE, 0xF)
    assert await _source_out(dut) == 0xF, (
        f"REA-REQ-701 failed: after SOURCE<=0xF, source_out="
        f"0x{int(dut.source_o.value):X}, expected 0xF"
    )
    await _jtag_write(dut, ADDR_SOURCE, 0x0)
    assert await _source_out(dut) == 0x0, (
        "REA-REQ-701 failed: source_out did not re-gate after full release"
    )
    dut._log.info("REA-REQ-701 PASS — source_out release/re-gate crosses CDC")


# ── REA-REQ-702: only the low G_NUM_SOURCE bits reach the port ───────


@cocotb.test()
@requires("REA-REQ-702")
async def test_rea_req_702_source_upper_bits_isolated(dut):
    """Bits above G_NUM_SOURCE are stored (round-trip readback) but never
    drive source_out — the port width is the contract, not the 32-bit
    register. Guards against a wide-slice bug leaking phantom control bits
    into the design."""
    await _start_clocks(dut)
    await _reset(dut)

    # Set ONLY bits above the exposed field (0xF0 with G_NUM_SOURCE=4).
    await _jtag_write(dut, ADDR_SOURCE, 0xF0)
    assert await _source_out(dut) == 0x0, (
        f"REA-REQ-702 failed: upper SOURCE bits leaked to source_out="
        f"0x{int(dut.source_o.value):X}, expected 0x0 (port must expose "
        f"only the low {G_NUM_SOURCE} bits)"
    )
    # ...but the full 32-bit register still round-trips on readback.
    rb = await _jtag_read(dut, ADDR_SOURCE)
    assert rb == 0xF0, (
        f"REA-REQ-702 failed: SOURCE readback 0x{rb:X}, expected 0xF0 "
        f"(upper bits must still be stored)"
    )

    # Mixed word: low nibble drives the port, upper nibble does not.
    await _jtag_write(dut, ADDR_SOURCE, 0xA3)
    assert await _source_out(dut) == 0x3, (
        f"REA-REQ-702 failed: source_out=0x{int(dut.source_o.value):X}, "
        f"expected 0x3 (low {G_NUM_SOURCE} bits of 0xA3)"
    )
    dut._log.info("REA-REQ-702 PASS — upper SOURCE bits isolated from port")


if __name__ == "__main__":
    main()
