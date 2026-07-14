# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""Field-repro attempt: register reads of values with bit0=1 return 0xFFFFFFFF.

A field bench (Arria 10, wide 704-bit REA, System Console SLD two-scan reads)
reports: any register read whose VALUE has bit0=1 returns 0xFFFFFFFF, while
bit0=0 values (including 0xFFFFFFFE) round-trip exactly. VERSION (0x52454105,
odd) reads FF; SAMPLE_W/DEPTH/BUILD_ID/FEATURES (even) read correctly. The
write path is fine.

This test replicates the field experiment VERBATIM at the field build's exact
generics (704-bit sample, depth 1024, 3 sources, 4 comparator slots) against
the vendor-neutral core (mock TAP; the vendor wrapper is a pure passthrough).
It runs the probe vectors in BOTH orders to also discriminate the alternate
hypothesis (every-second-transaction failure, which the field data cannot
separate from value-bit0 keying — every bit0=1 vector there also sat at an
odd transaction position).

Outcomes:
  - reproduces  → RTL defect; waveform pins it.
  - stays green → the defect is NOT in the vendor-neutral core at this
    config; suspicion moves to the vendor layer / hub interaction / bench
    integration (unmodeled condition), per the sim-passing-but-silicon-fails
    discipline.
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

# Field build shape (FEATURES read 0x00010304 on the bench: trig_conds=4,
# num_source=3, wide_sample=1) at the field's EXACT 704-bit probe.
GENERICS = {
    # RTL-P2.876 raised C_MAX_SAMPLE_W 256 → 1024, so the field's 704-bit
    # build now ELABORATES (previously the C_MAX_SAMPLE_W guard REFUSED it —
    # which was correct: the field silicon at 704 was out of the old contract,
    # and its bit0=1→0xFFFFFFFF signature was out-of-ceiling UNDEFINED silicon
    # from vendor synth ignoring the runtime assert, now made un-ignorable by
    # RTL-P2.895). This test now runs the field experiment VERBATIM at 704 and
    # must PASS — proving the core is clean at the field's own width.
    "G_SAMPLE_W": 704,
    "G_DEPTH": 1024,
    "G_TIMESTAMP_W": 0,
    "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": 4,
    "G_NUM_SOURCE": 3,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[4] / "ip" / "routertl" / "rea" / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

ADDR_VERSION = 0x00
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_DATA_WORD_SEL = 0xCC

C_REA_VERSION = 0x52454107  # v0.7 tier. bit0=1: the
                            # field's most visible victim (odd magic read FF)

SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS = 25.0

# The field bench's exact PRETRIG probe vectors, in its exact order.
FIELD_VECTORS = [
    0x12345678,  # bit0=0 → field: MATCH
    0x12345679,  # bit0=1 → field: 0xFFFFFFFF
    0xAAAAAAAA,  # bit0=0 → field: MATCH
    0xAAAAAAAB,  # bit0=1 → field: 0xFFFFFFFF
    0xFFFFFFFE,  # bit0=0 → field: MATCH (31 ones, exact)
    0x00000001,  # bit0=1 → field: 0xFFFFFFFF
    0x00000000,  # bit0=0 → field: MATCH
]


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_field_bit0_parity",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst.value = 1
    dut.arst.value = 1
    dut.tdi.value = 0
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 0
    dut.sel.value = 0
    dut.probe_in.value = 0
    await ClockCycles(dut.tck, 4)
    dut.sample_rst.value = 0
    dut.arst.value = 0
    await ClockCycles(dut.tck, 1)


async def _capture_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 1
    dut.shift_en.value = 0
    dut.update.value = 0
    await RisingEdge(dut.tck)
    dut.capture.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    from cocotb.triggers import NextTimeStep

    dut.sel.value = 1
    dut.capture.value = 0
    dut.update.value = 0
    dut.shift_en.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo.value) & 1) << i
        await RisingEdge(dut.tck)
    dut.shift_en.value = 0
    return out


async def _update_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 1
    await RisingEdge(dut.tck)
    dut.update.value = 0
    dut.sel.value = 0


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
    await ClockCycles(dut.tck, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


@cocotb.test()
@requires("REA-REQ-010", "REA-REQ-012")
async def test_field_bit0_vectors_round_trip(dut):
    """The field bench's exact PRETRIG/POSTTRIG series, exact order: every
    vector must round-trip verbatim — a 0xFFFFFFFF on any bit0=1 vector is
    the field defect reproducing."""
    await _start_clocks(dut)
    await _reset(dut)

    for v in FIELD_VECTORS:
        await _jtag_write(dut, ADDR_PRETRIG, v)
        got = await _jtag_read(dut, ADDR_PRETRIG)
        assert got == v, (
            f"FIELD DEFECT REPRODUCED: PRETRIG wrote 0x{v:08X} "
            f"(bit0={v & 1}) read 0x{got:08X}"
        )

    # POSTTRIG control run from the field report.
    for v in (0xFFFFFFFE, 0x00000003, 0x00000002):
        await _jtag_write(dut, ADDR_POSTTRIG, v)
        got = await _jtag_read(dut, ADDR_POSTTRIG)
        assert got == v, (
            f"FIELD DEFECT REPRODUCED: POSTTRIG wrote 0x{v:08X} "
            f"(bit0={v & 1}) read 0x{got:08X}"
        )


@cocotb.test()
@requires("REA-REQ-010", "REA-REQ-012")
async def test_field_bit0_vectors_reversed_order(dut):
    """Same vectors, REVERSED execution order. If a failure followed the
    POSITION (alternating transactions) rather than the VALUE, this run
    fails on different vectors than the forward run — the discriminator the
    field data lacks."""
    await _start_clocks(dut)
    await _reset(dut)

    for v in reversed(FIELD_VECTORS):
        await _jtag_write(dut, ADDR_PRETRIG, v)
        got = await _jtag_read(dut, ADDR_PRETRIG)
        assert got == v, (
            f"POSITION-KEYED DEFECT: reversed order PRETRIG wrote 0x{v:08X} "
            f"read 0x{got:08X}"
        )


@cocotb.test()
@requires("REA-REQ-010")
async def test_field_version_and_selector_sweep(dut):
    """VERSION (odd magic) must read back exactly, twice in a row; the
    DATA_WORD_SEL 0..21 sweep must echo every value (field saw odd
    selectors read FF)."""
    await _start_clocks(dut)
    await _reset(dut)

    for attempt in range(2):
        got = await _jtag_read(dut, ADDR_VERSION)
        assert got == C_REA_VERSION, (
            f"FIELD DEFECT REPRODUCED: VERSION read #{attempt} = 0x{got:08X} "
            f"(expected 0x{C_REA_VERSION:08X}, bit0=1)"
        )

    for sel in range(22):
        await _jtag_write(dut, ADDR_DATA_WORD_SEL, sel)
        got = await _jtag_read(dut, ADDR_DATA_WORD_SEL)
        assert got == sel, (
            f"FIELD DEFECT REPRODUCED: DATA_WORD_SEL wrote {sel} "
            f"read 0x{got:08X}"
        )


if __name__ == "__main__":
    main()
