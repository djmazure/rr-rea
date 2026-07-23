# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_regbank — per-condition comparator-array expansion (RTL-P3.647).
#
# The compact slot written via COND_SEL/COND_CFG/COND_VAL (paged like
# TRIG_WORD_SEL) must expand into the shifted full-width value + field mask +
# op + valid the FSM consumes. Elaborated at G_TRIG_CONDS=4, G_SAMPLE_W=16.
#
# Run via: rr sim run --ip <rea-dir> test_rea_regbank_array

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

G_SAMPLE_W = 16
G_TRIG_CONDS = 4
GENERICS = {"G_SAMPLE_W": G_SAMPLE_W, "G_DEPTH": 4096,
            "G_TIMESTAMP_W": 32, "G_NUM_CHAN": 1, "G_TRIG_CONDS": G_TRIG_CONDS}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_COND_SEL = 0x30
ADDR_COND_CFG = 0x34
ADDR_COND_VAL = 0x38

OP_EQ, OP_NE, OP_LT, OP_GT = 0, 1, 2, 3


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank_array",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


CLK_NS = 25.0


def _cfg(valid: int, op: int, width: int, lsb: int) -> int:
    """COND_CFG word: {valid[31], op[27:24], width[23:16], lsb[15:8]}."""
    return ((valid & 1) << 31) | ((op & 0xF) << 24) | ((width & 0xFF) << 16) \
        | ((lsb & 0xFF) << 8)


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.jtag_clk, CLK_NS, unit="ns").start())


async def _reset(dut):
    dut.jtag_rst.value = 1
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_addr.value = 0
    dut.armed_in.value = 0
    dut.triggered_in.value = 0
    dut.done_in.value = 0
    dut.overflow_in.value = 0
    dut.start_ptr_in.value = 0
    await ClockCycles(dut.jtag_clk, 4)
    dut.jtag_rst.value = 0
    await ClockCycles(dut.jtag_clk, 1)


async def _write(dut, addr: int, data: int):
    dut.wr_addr.value = addr
    dut.wr_data.value = data
    dut.wr_en.value = 1
    await RisingEdge(dut.jtag_clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.jtag_clk)


async def _read(dut, addr: int) -> int:
    dut.rd_addr.value = addr
    await ClockCycles(dut.jtag_clk, 2)
    return int(dut.rd_data.value)


def _slot(vec: int, k: int, width: int) -> int:
    return (vec >> (k * width)) & ((1 << width) - 1)


async def _write_cond(dut, slot: int, cfg: int, val: int):
    await _write(dut, ADDR_COND_SEL, slot)
    await _write(dut, ADDR_COND_CFG, cfg)
    await _write(dut, ADDR_COND_VAL, val)


@cocotb.test()
@requires("REA-REQ-608")
async def test_cond_expansion_value_mask_op(dut):
    """Two slots expand to the right shifted value, field mask, op, valid.
    slot0: [3:0] LT 5 ; slot1: [7:4] EQ 1."""
    await _start_clk(dut)
    await _reset(dut)

    await _write_cond(dut, 0, _cfg(1, OP_LT, 4, 0), 5)
    await _write_cond(dut, 1, _cfg(1, OP_EQ, 4, 4), 1)
    await ClockCycles(dut.jtag_clk, 2)

    masks = int(dut.cond_masks_out.value)
    values = int(dut.cond_values_out.value)
    ops = int(dut.cond_ops_out.value)
    valid = int(dut.cond_valid_out.value)

    # slot0: field [3:0]
    assert _slot(masks, 0, G_SAMPLE_W) == 0x000F, "slot0 mask"
    assert _slot(values, 0, G_SAMPLE_W) == 0x0005, "slot0 value (5 at lsb 0)"
    assert _slot(ops, 0, 4) == OP_LT, "slot0 op LT"
    # slot1: field [7:4], value 1 shifted to lsb 4 → 0x10
    assert _slot(masks, 1, G_SAMPLE_W) == 0x00F0, "slot1 mask shifted to lsb 4"
    assert _slot(values, 1, G_SAMPLE_W) == 0x0010, "slot1 value 1<<4"
    assert _slot(ops, 1, 4) == OP_EQ, "slot1 op EQ"
    # valids
    assert valid & 0b0011 == 0b0011, "slots 0,1 valid"
    assert valid & 0b1100 == 0, "slots 2,3 invalid"

    dut._log.info("REA-REQ-608 PASS — cond slots expand to shifted value/mask/op")


@cocotb.test()
@requires("REA-REQ-608")
async def test_cond_paged_readback(dut):
    """COND_CFG/COND_VAL read back the slot selected by COND_SEL."""
    await _start_clk(dut)
    await _reset(dut)

    cfg0, cfg1 = _cfg(1, OP_GT, 8, 0), _cfg(1, OP_NE, 4, 8)
    await _write_cond(dut, 0, cfg0, 0x12)
    await _write_cond(dut, 1, cfg1, 0x34)

    await _write(dut, ADDR_COND_SEL, 0)
    assert (await _read(dut, ADDR_COND_CFG)) == cfg0
    assert (await _read(dut, ADDR_COND_VAL)) == 0x12
    await _write(dut, ADDR_COND_SEL, 1)
    assert (await _read(dut, ADDR_COND_CFG)) == cfg1
    assert (await _read(dut, ADDR_COND_VAL)) == 0x34
    assert (await _read(dut, ADDR_COND_SEL)) == 1

    dut._log.info("REA-REQ-608 PASS — COND_CFG/VAL page by COND_SEL")


@cocotb.test()
@requires("REA-REQ-608")
async def test_cond_out_of_range_sel_dropped(dut):
    """A write with COND_SEL beyond G_TRIG_CONDS-1 mutates no real slot."""
    await _start_clk(dut)
    await _reset(dut)

    await _write_cond(dut, 0, _cfg(1, OP_EQ, 4, 0), 0x7)
    # Point at slot 9 (only 0..3 exist) and write junk.
    await _write_cond(dut, 9, _cfg(1, OP_GT, 8, 0), 0xFFFF)
    await ClockCycles(dut.jtag_clk, 2)

    # slot0 untouched.
    await _write(dut, ADDR_COND_SEL, 0)
    assert (await _read(dut, ADDR_COND_VAL)) == 0x7
    assert _slot(int(dut.cond_values_out.value), 0, G_SAMPLE_W) == 0x7
    # no slot is valid beyond the two we know — junk didn't land anywhere real.
    assert int(dut.cond_valid_out.value) & 0b1110 == 0

    dut._log.info("REA-REQ-608 PASS — out-of-range COND_SEL write dropped")


@cocotb.test()
@requires("REA-REQ-012")
async def test_cond_writes_dont_alias_legacy(dut):
    """COND_SEL/CFG/VAL writes must not bleed into the legacy RW slots."""
    await _start_clk(dut)
    await _reset(dut)
    await _write(dut, 0x14, 0xCAFE_F00D)  # PRETRIG
    await _write_cond(dut, 0, _cfg(1, OP_LT, 4, 0), 0xABCD)
    assert (await _read(dut, 0x14)) == 0xCAFE_F00D, "PRETRIG must be intact"
    dut._log.info("REA-REQ-012 PASS — COND writes don't alias legacy slots")


if __name__ == "__main__":
    main()
