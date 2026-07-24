# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_regbank — 704-bit-probe wide paging + wide comparator conditions.
#
# RTL-P2.876 raises C_MAX_SAMPLE_W 256 → 1024 and extends the per-condition
# field_lsb to 11 bits (lsb_hi in COND_CFG[30:28]) so a comparator field can
# sit above bit 255. Elaborated at the field's exact G_SAMPLE_W=704 (22 trig
# words). Covers:
#   REA-REQ-016 — 22-word banked trig value/mask paging at a >256 width.
#   REA-REQ-017 — a COND_CFG slot with field_lsb>=256 expands to a full-width
#                 mask/value shifted to that HIGH offset (lsb_hi decode), and
#                 FEATURES[17]=wide_cond is set (G_SAMPLE_W>256).
#
# The 64-bit back-compat paging + FEATURES[16] wide_sample are proven by
# test_rea_regbank_wide.py; this file proves the >256 extension.
#
# Run via: rr sim run --ip <rea-dir> test_rea_regbank_wide704
#     or:  PYTHONPATH=sim/cocotb:. python test_rea_regbank_wide704.py

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

# The field's exact 704-bit probe → ceil(704/32)=22 banked trigger words.
G_SAMPLE_W = 704
G_TRIG_CONDS = 4
N_WORDS = (G_SAMPLE_W + 31) // 32  # 22
GENERICS = {
    "G_SAMPLE_W": G_SAMPLE_W, "G_DEPTH": 1024,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": G_TRIG_CONDS, "G_NUM_SOURCE": 3,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_TRIG_VALUE    = 0x24
ADDR_TRIG_MASK     = 0x28
ADDR_TRIG_WORD_SEL = 0x2C
ADDR_COND_SEL      = 0x30
ADDR_COND_CFG      = 0x34
ADDR_COND_VAL      = 0x38
ADDR_FEATURES      = 0xD0

OP_EQ = 0

# FEATURES at G_SAMPLE_W=704 (>256): trig_conds=4, num_source=3, wide_sample[16],
# wide_cond[17]. Hard-coded per ROUTERTL-002: 0x0003_0304.
EXPECTED_FEATURES = (4 << 0) | (3 << 8) | (1 << 16) | (1 << 17) | (1 << 19)


def _cfg(valid: int, op: int, width: int, lsb: int) -> int:
    """COND_CFG: {valid[31], lsb_hi[30:28], op[27:24], width[23:16], lsb_lo[15:8]}."""
    return ((valid & 1) << 31) | (((lsb >> 8) & 0x7) << 28) | ((op & 0xF) << 24) \
        | ((width & 0xFF) << 16) | ((lsb & 0xFF) << 8)


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank_wide704",
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


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.jtag_clk_i, CLK_NS, unit="ns").start())


async def _reset(dut):
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


async def _write(dut, addr: int, data: int):
    dut.wr_addr_i.value = addr
    dut.wr_data_i.value = data
    dut.wr_en_i.value = 1
    await RisingEdge(dut.jtag_clk_i)
    dut.wr_en_i.value = 0


async def _read(dut, addr: int) -> int:
    dut.rd_addr_i.value = addr
    await ClockCycles(dut.jtag_clk_i, 2)
    return int(dut.rd_data_o.value)


def _slot(vec: int, k: int, width: int) -> int:
    return (vec >> (k * width)) & ((1 << width) - 1)


@cocotb.test()
@requires("REA-REQ-016")
async def test_704_bit_trig_paging_22_words(dut):
    """All 22 banked trigger words page independently via TRIG_WORD_SEL and
    reassemble into the full 704-bit trig_value_out. Hard-coded per-word
    pattern per ROUTERTL-002 (distinct per word to catch aliasing)."""
    await _start_clk(dut)
    await _reset(dut)

    # Distinct non-trivial word constants (word index folded in).
    words = [((0xC0DE_0000 + (w * 0x0101_0101)) & 0xFFFF_FFFF) for w in range(N_WORDS)]
    for w in range(N_WORDS):
        await _write(dut, ADDR_TRIG_WORD_SEL, w)
        await _write(dut, ADDR_TRIG_VALUE, words[w])

    # Read each word back through the paging window.
    for w in range(N_WORDS):
        await _write(dut, ADDR_TRIG_WORD_SEL, w)
        rd = await _read(dut, ADDR_TRIG_VALUE)
        assert rd == words[w], f"word{w} readback 0x{rd:08X} != 0x{words[w]:08X}"

    # Full-width comparator output = little-endian concat of all 22 words,
    # masked to 704 bits (the top word's bits [31:16] are above G_SAMPLE_W).
    expected = 0
    for w in range(N_WORDS):
        expected |= words[w] << (32 * w)
    expected &= (1 << G_SAMPLE_W) - 1
    observed = int(dut.trig_value_o.value)
    assert observed == expected, (
        f"trig_value_out mismatch at 704: 0x{observed:x} != 0x{expected:x}"
    )
    dut._log.info("REA-REQ-016 PASS — 704-bit trig value pages across 22 words")


@cocotb.test()
@requires("REA-REQ-017")
async def test_features_wide_cond_bit_set_at_704(dut):
    """FEATURES[17]=wide_cond (and [16]=wide_sample) is set at G_SAMPLE_W=704."""
    await _start_clk(dut)
    await _reset(dut)
    feat = await _read(dut, ADDR_FEATURES)
    assert feat == EXPECTED_FEATURES, (
        f"FEATURES at 704: read 0x{feat:08X}, expected 0x{EXPECTED_FEATURES:08X} "
        "(wide_sample[16] + wide_cond[17] set)"
    )
    assert (feat >> 17) & 1 == 1, "wide_cond bit[17] must be set for G_SAMPLE_W>256"
    dut._log.info("REA-REQ-017 PASS — FEATURES wide_cond bit set at 704")


@cocotb.test()
@requires("REA-REQ-017")
async def test_condition_field_lsb_above_255_expands_to_high_offset(dut):
    """A COND_CFG slot with field_lsb=512 (needs the lsb_hi extension) expands
    to a full-width mask/value shifted to bit 512 — the wrong-bits defect a
    core without the 11-bit lsb would produce. slot0: [515:512] EQ 0xA."""
    await _start_clk(dut)
    await _reset(dut)

    LSB, WIDTH, VAL = 512, 4, 0xA
    await _write(dut, ADDR_COND_SEL, 0)
    await _write(dut, ADDR_COND_CFG, _cfg(1, OP_EQ, WIDTH, LSB))
    await _write(dut, ADDR_COND_VAL, VAL)
    await ClockCycles(dut.jtag_clk_i, 2)

    masks = int(dut.cond_masks_o.value)
    values = int(dut.cond_values_o.value)
    slot0_mask = _slot(masks, 0, G_SAMPLE_W)
    slot0_value = _slot(values, 0, G_SAMPLE_W)

    # 4-bit field at lsb 512 → mask 0xF << 512, value 0xA << 512.
    assert slot0_mask == (0xF << LSB), (
        f"mask not at bit {LSB}: got 0x{slot0_mask:x} (a core decoding only the "
        "low 8 bits of field_lsb would place it at bit 0 — the wrong-bits bug)"
    )
    assert slot0_value == (VAL << LSB), f"value not at bit {LSB}: 0x{slot0_value:x}"
    assert (await _read(dut, ADDR_COND_CFG)) == _cfg(1, OP_EQ, WIDTH, LSB), \
        "COND_CFG must round-trip the lsb_hi bits verbatim"
    dut._log.info("REA-REQ-017 PASS — field_lsb>=256 expands to the high offset")


if __name__ == "__main__":
    main()
