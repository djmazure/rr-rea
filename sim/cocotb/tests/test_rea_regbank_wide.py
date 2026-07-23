# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_regbank — wide (banked) trigger value/mask unit tests.
#
# Covers REA-REQ-013 (RTL-P2.658(b)): for G_SAMPLE_W > 32 the trigger
# value/mask are banked into ceil(W/32) 32-bit words paged via
# TRIG_WORD_SEL (0x2C). Elaborated at G_SAMPLE_W=64 (2 words) — a width
# the legacy single-32-bit-register path could never have driven.
#
# The G_SAMPLE_W<=32 (single-word) back-compat path is covered by
# test_rea_regbank.py; this file proves the multiword extension.
#
# Run via: rr sim run --ip <rea-dir> test_rea_regbank_wide
#     or:  python test_rea_regbank_wide.py  (with PYTHONPATH set)

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

# 64-bit probe → 2 banked trigger words. Deliberately > 32 so the
# paging path is the ONLY way the value/mask can be fully programmed.
G_SAMPLE_W = 64
GENERICS = {
    "G_SAMPLE_W": G_SAMPLE_W, "G_DEPTH": 4096,
    "G_TIMESTAMP_W": 32, "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# Register addresses — must match rr_rea_pkg.vhd (ROUTERTL-002).
ADDR_TRIG_VALUE    = 0x24
ADDR_TRIG_MASK     = 0x28
ADDR_TRIG_WORD_SEL = 0x2C
ADDR_FEATURES      = 0xD0

# At G_SAMPLE_W=64 (>32) the FEATURES wide-sample bit[16] must be set;
# entity defaults G_TRIG_CONDS=4, G_NUM_SOURCE=1 give the low bytes
# (RTL-P3.1198). Hard-coded per ROUTERTL-002.
EXPECTED_FEATURES_WIDE = (4 << 0) | (1 << 8) | (1 << 16) | (1 << 18)


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank_wide",
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


async def _read(dut, addr: int) -> int:
    dut.rd_addr.value = addr
    await ClockCycles(dut.jtag_clk, 2)
    return int(dut.rd_data.value)


async def _write_word(dut, base_addr: int, word: int, data: int):
    """Select bank `word` then write `data` into base_addr's window."""
    await _write(dut, ADDR_TRIG_WORD_SEL, word)
    await _write(dut, base_addr, data)


# ── REA-REQ-013: wide value/mask page in and drive the full-width out ──


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_wide_value_paging(dut):
    """At G_SAMPLE_W=64 the two trigger words page independently via
    TRIG_WORD_SEL; the reassembled 64-bit value drives trig_value_out.
    Hard-coded patterns per ROUTERTL-002."""
    await _start_clk(dut)
    await _reset(dut)

    # Distinct, non-trivial words (no all-0 / all-1) to catch aliasing.
    w0 = 0xAAAA_5555
    w1 = 0x1234_5678
    await _write_word(dut, ADDR_TRIG_VALUE, 0, w0)
    await _write_word(dut, ADDR_TRIG_VALUE, 1, w1)

    # Read each word back through the same paging window.
    await _write(dut, ADDR_TRIG_WORD_SEL, 0)
    rd0 = await _read(dut, ADDR_TRIG_VALUE)
    await _write(dut, ADDR_TRIG_WORD_SEL, 1)
    rd1 = await _read(dut, ADDR_TRIG_VALUE)
    assert rd0 == w0, f"word0 readback 0x{rd0:08X} != 0x{w0:08X}"
    assert rd1 == w1, f"word1 readback 0x{rd1:08X} != 0x{w1:08X}"

    # The full-width comparator output is little-endian {w1, w0}.
    expected = (w1 << 32) | w0
    observed = int(dut.trig_value_out.value)
    assert observed == expected, (
        f"trig_value_out 0x{observed:016X} != 0x{expected:016X}"
    )

    dut._log.info("REA-REQ-013 PASS — wide value pages + drives full width")


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_wide_mask_paging(dut):
    """Same banking for the mask, and value/mask use the SAME word-sel
    decode so a single SEL set programs both words' worth."""
    await _start_clk(dut)
    await _reset(dut)

    mw0 = 0xFFFF_0000
    mw1 = 0x00FF_FF00
    await _write_word(dut, ADDR_TRIG_MASK, 0, mw0)
    await _write_word(dut, ADDR_TRIG_MASK, 1, mw1)

    # Read each word back through the paging window — this also lets the
    # combinational trig_mask_out settle before we sample it.
    await _write(dut, ADDR_TRIG_WORD_SEL, 0)
    assert (await _read(dut, ADDR_TRIG_MASK)) == mw0
    await _write(dut, ADDR_TRIG_WORD_SEL, 1)
    assert (await _read(dut, ADDR_TRIG_MASK)) == mw1

    expected = (mw1 << 32) | mw0
    observed = int(dut.trig_mask_out.value)
    assert observed == expected, (
        f"trig_mask_out 0x{observed:016X} != 0x{expected:016X}"
    )

    dut._log.info("REA-REQ-013 PASS — wide mask pages + drives full width")


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_word_sel_roundtrips_and_clamps(dut):
    """TRIG_WORD_SEL reads back what was written, and a write to an
    out-of-range bank (>= C_TRIG_WORDS) is dropped — no word mutated."""
    await _start_clk(dut)
    await _reset(dut)

    # Seed both real words.
    await _write_word(dut, ADDR_TRIG_VALUE, 0, 0xCAFE_F00D)
    await _write_word(dut, ADDR_TRIG_VALUE, 1, 0xDEAD_BEEF)

    # SEL round-trips.
    await _write(dut, ADDR_TRIG_WORD_SEL, 1)
    sel = await _read(dut, ADDR_TRIG_WORD_SEL)
    assert sel == 1, f"TRIG_WORD_SEL readback {sel} != 1"

    # Point SEL at a non-existent bank (only words 0..1 exist) and write.
    await _write_word(dut, ADDR_TRIG_VALUE, 5, 0x1111_2222)

    # Neither real word changed — the out-of-range write was dropped.
    await _write(dut, ADDR_TRIG_WORD_SEL, 0)
    assert (await _read(dut, ADDR_TRIG_VALUE)) == 0xCAFE_F00D
    await _write(dut, ADDR_TRIG_WORD_SEL, 1)
    assert (await _read(dut, ADDR_TRIG_VALUE)) == 0xDEAD_BEEF

    # And the comparator output still reflects only the real words.
    expected = (0xDEAD_BEEF << 32) | 0xCAFE_F00D
    assert int(dut.trig_value_out.value) == expected

    dut._log.info("REA-REQ-013 PASS — SEL round-trips; OOB bank write dropped")


@cocotb.test()
@requires("REA-REQ-013")
async def test_rea_req_013_word0_back_compat(dut):
    """With SEL at its reset value (0), 0x24/0x28 behave exactly like the
    legacy single-word path: the low 32 bits of the output track word 0
    and the upper word stays 0 until paged."""
    await _start_clk(dut)
    await _reset(dut)

    # No SEL write at all — rely on the reset default of 0.
    await _write(dut, ADDR_TRIG_VALUE, 0x0000_0042)
    await _write(dut, ADDR_TRIG_MASK,  0x0000_00FF)

    assert (await _read(dut, ADDR_TRIG_VALUE)) == 0x42
    assert (await _read(dut, ADDR_TRIG_MASK)) == 0xFF
    # Upper word never written → output is just word 0.
    assert int(dut.trig_value_out.value) == 0x42
    assert int(dut.trig_mask_out.value) == 0xFF

    dut._log.info("REA-REQ-013 PASS — SEL=0 reset default is legacy-identical")


@cocotb.test()
@requires("REA-REQ-015")
async def test_rea_req_015_features_wide_sample_bit(dut):
    """At G_SAMPLE_W=64 the FEATURES (0xD0) wide-sample bit[16] is set,
    so a host reading the fingerprint knows this build pages capture
    data wider than 32 bits (RTL-P3.1198). Hard-coded per ROUTERTL-002."""
    await _start_clk(dut)
    await _reset(dut)

    feat = await _read(dut, ADDR_FEATURES)
    assert feat == EXPECTED_FEATURES_WIDE, (
        f"FEATURES mismatch at G_SAMPLE_W=64: read 0x{feat:08X}, "
        f"expected 0x{EXPECTED_FEATURES_WIDE:08X} (wide-sample bit set)"
    )

    dut._log.info("REA-REQ-015 PASS — FEATURES wide-sample bit reflects G_SAMPLE_W>32")


if __name__ == "__main__":
    main()
