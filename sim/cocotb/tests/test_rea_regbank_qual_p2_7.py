# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.7 — storage-qualifier registers at the regbank (REA-REQ-958, 952).

Drives rr_rea_regbank directly: QUAL_MODE/SEL/CFG/VAL read back on `rd_data_o`,
an out-of-range slot page drops writes, FEATURES/VERSION advertise the
qualifier, and each slot's compact COND_CFG-style word expands to the
full-width field on `qual_masks_o` / `qual_values_o` exactly as the comparator
array does — checked against masks computed by hand here, at a probe wider than
32 bits so the shifted field crosses a word boundary.
"""

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

SAMPLE_W = 40
QUAL_CONDS = 3
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": 64, "G_TIMESTAMP_W": 32,
            "G_NUM_CHAN": 1, "G_QUAL_CONDS": QUAL_CONDS}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_VERSION = 0x00
ADDR_FEATURES = 0xD0
ADDR_QUAL_MODE = 0xB4
ADDR_QUAL_SEL = 0xB8
ADDR_QUAL_CFG = 0xBC
ADDR_QUAL_VAL = 0xC0


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank_qual_p2_7",
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


async def _start(dut) -> None:
    cocotb.start_soon(Clock(dut.jtag_clk_i, 25.0, unit="ns").start())
    dut.jtag_rst_i.value = 1
    dut.wr_en_i.value = 0
    dut.wr_addr_i.value = 0
    dut.wr_data_i.value = 0
    dut.rd_addr_i.value = 0
    dut.armed_i.value = 0
    dut.triggered_i.value = 0
    dut.done_i.value = 0
    dut.overflow_i.value = 0
    dut.start_ptr_i.value = 0
    await ClockCycles(dut.jtag_clk_i, 4)
    dut.jtag_rst_i.value = 0
    await ClockCycles(dut.jtag_clk_i, 1)


async def _write(dut, addr: int, data: int) -> None:
    dut.wr_addr_i.value = addr
    dut.wr_data_i.value = data
    dut.wr_en_i.value = 1
    await RisingEdge(dut.jtag_clk_i)
    dut.wr_en_i.value = 0


async def _read(dut, addr: int) -> int:
    dut.rd_addr_i.value = addr
    await ClockCycles(dut.jtag_clk_i, 2)
    return int(dut.rd_data_o.value)


def _cfg(op: int, lsb: int, width: int, valid: bool = True) -> int:
    return ((int(valid) << 31) | (((lsb >> 8) & 0x7) << 28) | ((op & 0xF) << 24)
            | ((width & 0xFF) << 16) | ((lsb & 0xFF) << 8))


def _slot(vec: int, k: int, w: int) -> int:
    return (vec >> (k * w)) & ((1 << w) - 1)


@cocotb.test()
@requires("REA-REQ-958")
async def test_rea_req_958_registers_read_back_on_rd_data_o(dut):
    """Reset 0; QUAL_MODE keeps [1:0]; slots read back per page on `rd_data_o`;
    a page >= G_QUAL_CONDS drops writes and reads 0 without aliasing;
    FEATURES[22]=1, [27:24]=G_QUAL_CONDS; VERSION 0x5245410D."""
    await _start(dut)
    assert await _read(dut, ADDR_VERSION) == 0x5245410D
    features = await _read(dut, ADDR_FEATURES)
    assert (features >> 22) & 1 == 1
    assert (features >> 24) & 0xF == QUAL_CONDS
    for addr in (ADDR_QUAL_MODE, ADDR_QUAL_SEL, ADDR_QUAL_CFG, ADDR_QUAL_VAL):
        assert await _read(dut, addr) == 0

    await _write(dut, ADDR_QUAL_MODE, 0xFFFF_FFFE)
    assert await _read(dut, ADDR_QUAL_MODE) == 0x2
    assert int(dut.qual_mode_o.value) == 0b10

    words = {k: (0x8000_0000 | (k << 8) | 0x0001_0000, 0x1111_1111 * (k + 1))
             for k in range(QUAL_CONDS)}
    for k, (cfg, val) in words.items():
        await _write(dut, ADDR_QUAL_SEL, k)
        await _write(dut, ADDR_QUAL_CFG, cfg)
        await _write(dut, ADDR_QUAL_VAL, val)
    for k, (cfg, val) in words.items():
        await _write(dut, ADDR_QUAL_SEL, k)
        assert await _read(dut, ADDR_QUAL_SEL) == k
        assert await _read(dut, ADDR_QUAL_CFG) == cfg, f"slot {k} cfg"
        assert await _read(dut, ADDR_QUAL_VAL) == val, f"slot {k} val"

    await _write(dut, ADDR_QUAL_SEL, QUAL_CONDS)
    await _write(dut, ADDR_QUAL_CFG, 0xFFFF_FFFF)
    await _write(dut, ADDR_QUAL_VAL, 0xFFFF_FFFF)
    assert await _read(dut, ADDR_QUAL_CFG) == 0
    assert await _read(dut, ADDR_QUAL_VAL) == 0
    for k, (cfg, val) in words.items():
        await _write(dut, ADDR_QUAL_SEL, k)
        assert await _read(dut, ADDR_QUAL_CFG) == cfg, f"slot {k} aliased"
        assert await _read(dut, ADDR_QUAL_VAL) == val, f"slot {k} aliased"


@cocotb.test()
@requires("REA-REQ-952")
async def test_rea_req_952_slot_expands_to_full_width_field(dut):
    """Slot {op, field_lsb, field_width, value} → `qual_masks_o`/`qual_values_o`
    full-width field, `qual_ops_o`/`qual_valid_o` passed through. Field [37:26]
    straddles the 32-bit boundary; a value wider than the field is truncated."""
    await _start(dut)
    cases = [  # (slot, op, lsb, width, value, valid)
        (0, 1, 0, 2, 0x0, True),        # the silicon use: do_wr|do_rd NE 0
        (1, 0, 26, 12, 0xFABC, True),   # value wider than the field
        (2, 4, 39, 1, 0x1, False),      # top bit, slot not valid
    ]
    for k, op, lsb, width, value, valid in cases:
        await _write(dut, ADDR_QUAL_SEL, k)
        await _write(dut, ADDR_QUAL_CFG, _cfg(op, lsb, width, valid))
        await _write(dut, ADDR_QUAL_VAL, value)
    await ClockCycles(dut.jtag_clk_i, 2)
    masks = int(dut.qual_masks_o.value)
    values = int(dut.qual_values_o.value)
    ops = int(dut.qual_ops_o.value)
    valid_bits = int(dut.qual_valid_o.value)
    for k, op, lsb, width, value, valid in cases:
        want_mask = ((1 << width) - 1) << lsb
        want_val = (value & ((1 << width) - 1)) << lsb
        assert _slot(masks, k, SAMPLE_W) == want_mask, (
            f"slot {k}: mask 0x{_slot(masks, k, SAMPLE_W):010X}, "
            f"want 0x{want_mask:010X}")
        assert _slot(values, k, SAMPLE_W) == want_val, (
            f"slot {k}: value 0x{_slot(values, k, SAMPLE_W):010X}, "
            f"want 0x{want_val:010X}")
        assert _slot(ops, k, 4) == op
        assert (valid_bits >> k) & 1 == int(valid)


if __name__ == "__main__":
    main()
