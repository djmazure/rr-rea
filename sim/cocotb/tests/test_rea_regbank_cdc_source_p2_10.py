# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.10 / REA-REQ-961: the words rr_rea_top crosses into sample_clk_i
leave the regbank straight from a jtag_clk_i register.

The expanded comparator and qualifier words used to be a combinational barrel
shift of the compact COND_*/QUAL_* slot registers, feeding rr_rea_sync_word
directly. Vivado flagged that as CDC-10 (combinational logic before a
synchronizer; 17 critical on the smallest build) and rr's routed-CDC gate
refused it. Being registered is observable in simulation: at the edge where the write
lands, a combinational output has already moved when the step settles; a
registered one moves exactly one jtag_clk_i edge later. So this test fails on
the old RTL and cannot pass on a combinational expansion.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

SAMPLE_W = 40
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": 64, "G_TIMESTAMP_W": 32,
            "G_NUM_CHAN": 1, "G_TRIG_CONDS": 2, "G_QUAL_CONDS": 2}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_COND_SEL, ADDR_COND_CFG, ADDR_COND_VAL = 0x30, 0x34, 0x38
ADDR_QUAL_SEL, ADDR_QUAL_CFG, ADDR_QUAL_VAL = 0xB8, 0xBC, 0xC0

COND_PORTS = ("cond_masks_o", "cond_values_o", "cond_ops_o", "cond_valid_o")
QUAL_PORTS = ("qual_masks_o", "qual_values_o", "qual_ops_o", "qual_valid_o")


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank_cdc_source_p2_10",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


def _cfg(op: int, lsb: int, width: int) -> int:
    """COND_CFG / QUAL_CFG: {valid[31], lsb_hi[30:28], op[27:24],
    width[23:16], lsb_lo[15:8]}."""
    return ((1 << 31) | (((lsb >> 8) & 0x7) << 28) | ((op & 0xF) << 24)
            | ((width & 0xFF) << 16) | ((lsb & 0xFF) << 8))


async def _start(dut) -> None:
    cocotb.start_soon(Clock(dut.jtag_clk_i, 25.0, unit="ns").start())
    for sig in ("wr_en_i", "wr_addr_i", "wr_data_i", "rd_addr_i", "armed_i",
                "triggered_i", "done_i", "overflow_i", "start_ptr_i"):
        getattr(dut, sig).value = 0
    dut.jtag_rst_i.value = 1
    await ClockCycles(dut.jtag_clk_i, 4)
    dut.jtag_rst_i.value = 0
    await ClockCycles(dut.jtag_clk_i, 2)


async def _write(dut, addr: int, data: int) -> None:
    """Present the write and return right after the edge that stores it."""
    dut.wr_addr_i.value = addr
    dut.wr_data_i.value = data
    dut.wr_en_i.value = 1
    await RisingEdge(dut.jtag_clk_i)
    dut.wr_en_i.value = 0


def _snap(dut, ports) -> dict:
    return {p: int(getattr(dut, p).value) for p in ports}


async def _check_registered(dut, sel: int, cfg_addr: int, val_addr: int,
                            ports, label: str) -> None:
    await _write(dut, sel, 1)
    await _write(dut, val_addr, 0xA5)      # slot still invalid: no change
    await ReadOnly()
    before = _snap(dut, ports)
    await RisingEdge(dut.jtag_clk_i)

    await _write(dut, cfg_addr, _cfg(op=1, lsb=30, width=8))
    await ReadOnly()
    at_write_edge = _snap(dut, ports)
    moved = [p for p in ports if at_write_edge[p] != before[p]]
    assert not moved, (
        f"{label}: {moved} changed in the SAME step as the {label}_CFG write "
        f"— combinational from the slot register into the synchronizer "
        f"(CDC-10, REA-REQ-961)")

    await RisingEdge(dut.jtag_clk_i)
    await ReadOnly()
    after = _snap(dut, ports)
    stuck = [p for p in ports if after[p] == before[p]]
    assert not stuck, (
        f"{label}: {stuck} never took the written slot one edge later — the "
        f"register is not wired, or the write did not land")
    # Slot 1, field [37:30] (crosses the 32-bit word boundary), value 0xA5.
    mask = _slot(after[f"{label.lower()}_masks_o"], 1)
    value = _slot(after[f"{label.lower()}_values_o"], 1)
    assert mask == 0xFF << 30, f"{label} slot-1 mask 0x{mask:010X}"
    assert value == 0xA5 << 30, f"{label} slot-1 value 0x{value:010X}"


def _slot(vec: int, k: int) -> int:
    return (vec >> (k * SAMPLE_W)) & ((1 << SAMPLE_W) - 1)


@cocotb.test()
@requires("REA-REQ-961")
async def test_rea_req_961_cond_words_leave_the_regbank_registered(dut):
    """`cond_masks_o`/`cond_values_o`/`cond_ops_o`/`cond_valid_o` do not move
    in the step of the COND_CFG write edge; they move one `jtag_clk_i` edge
    later, carrying the written slot."""
    await _start(dut)
    await _check_registered(dut, ADDR_COND_SEL, ADDR_COND_CFG, ADDR_COND_VAL,
                            COND_PORTS, "COND")


@cocotb.test()
@requires("REA-REQ-961")
async def test_rea_req_961_qual_words_leave_the_regbank_registered(dut):
    """`qual_masks_o`/`qual_values_o`/`qual_ops_o`/`qual_valid_o`: same
    contract for the storage-qualifier slots."""
    await _start(dut)
    await _check_registered(dut, ADDR_QUAL_SEL, ADDR_QUAL_CFG, ADDR_QUAL_VAL,
                            QUAL_PORTS, "QUAL")


if __name__ == "__main__":
    main()
