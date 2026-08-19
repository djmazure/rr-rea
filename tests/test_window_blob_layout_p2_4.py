# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.4 — the window blob layout is frozen (REA-REQ-911/912).

`sim/cocotb/tests/rea_window_blob.py` is the executable definition of the
payload every window transport (REA-P2.5 AXI-Stream, REA-ICE.1 UDP) must
reproduce, and the reference the cocotb contract test builds through both
doors. This structural test needs no simulator, so it runs in CI while the
cocotb job is blocked (RTL-T1.26), and it pins the layout with HARD-CODED
golden bytes (ROUTERTL-002) — if someone "tidies" the packer into big-endian,
cell-minor or plane-interleaved order, this goes red before any sim does.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sim" / "cocotb" / "tests"))

import rea_window_blob as blob  # noqa: E402


def test_words_per_cell_is_ceil_div_32():
    assert [blob.words_per_cell(w) for w in (1, 8, 12, 32, 33, 40, 64, 65, 704)] == \
        [1, 1, 1, 1, 2, 2, 2, 3, 22]
    with pytest.raises(ValueError):
        blob.words_per_cell(0)


def test_cell_words_are_little_endian_pages_zero_padded():
    # 40-bit cell 0xA5_0001_0140 → word0 = low 32 bits, word1 = 0xA5, no word2.
    assert blob.cell_words(0xA5_0001_0140, 40) == [0x0001_0140, 0x0000_00A5]
    # bits above plane_w are masked off, never leaked into a page
    assert blob.cell_words(0xFF_A5_0001_0140, 40) == [0x0001_0140, 0x0000_00A5]
    assert blob.cell_words(0x1_0000_0000, 32) == [0]


def test_golden_bytes_plane_major_le_words():
    """Two 40-bit sample cells + two 16-bit timestamps → exact bytes."""
    got = blob.pack_window([0xA5_0001_0140, 0xA5_0001_0141], 40,
                           [0x4000, 0x4001], 16)
    expect = bytes.fromhex(
        "40010100" "a5000000"    # sample cell 0: word0 LE, word1 LE
        "41010100" "a5000000"    # sample cell 1
        "00400000"               # ts cell 0   ← plane-major: AFTER all samples
        "01400000"               # ts cell 1
    )
    assert got == expect
    assert len(got) == blob.blob_len(40, 2, 16) == 2 * (2 + 1) * 4
    assert blob.unpack_window(got, 40, 2, 16) == \
        ([0xA5_0001_0140, 0xA5_0001_0141], [0x4000, 0x4001])


def test_no_timestamp_plane_when_timestamp_w_is_zero():
    got = blob.pack_window([1, 2, 3], 8)
    assert got == bytes.fromhex("01000000" "02000000" "03000000")
    assert blob.unpack_window(got, 8, 3) == ([1, 2, 3], None)
    with pytest.raises(ValueError):
        blob.pack_window([1], 8, [7], 0)          # ts plane without a width
    with pytest.raises(ValueError):
        blob.pack_window([1, 2], 8, [7], 16)      # planes of unequal length
    with pytest.raises(ValueError):
        blob.unpack_window(b"\0" * 5, 8, 1)      # wrong length is an error


def test_rotate_is_engine_order_from_start_ptr_across_wrap():
    """Cell i = physical[(START_PTR + i) mod DEPTH] — REA-REQ-912."""
    physical = list(range(100, 108))            # DEPTH = 8
    assert blob.rotate(physical, 6, 5, 8) == [106, 107, 100, 101, 102]
    assert blob.rotate(physical, 0, 8, 8) == physical
    with pytest.raises(ValueError):
        blob.rotate(physical, 8, 1, 8)
    with pytest.raises(ValueError):
        blob.rotate(physical, 0, 9, 8)


class _FakeMap:
    """A register map with the frozen offsets — enough for the walker."""

    def __init__(self, *, depth, sample_w, ts_w, capture_len, start_ptr,
                 done=True, cells=None, ts_cells=None):
        self.depth, self.sample_w, self.ts_w = depth, sample_w, ts_w
        self.regs = {
            blob.ADDR_STATUS: (blob.STATUS_DONE_BIT if done else 0),
            blob.ADDR_SAMPLE_W: sample_w,
            blob.ADDR_DEPTH: depth,
            blob.ADDR_CAPTURE_LEN: capture_len,
            blob.ADDR_TIMESTAMP_W: ts_w,
            blob.ADDR_START_PTR: start_ptr,
            blob.ADDR_FEATURES: (blob.FEAT_TIMESTAMP_BIT if ts_w else 0),
            blob.ADDR_DATA_WORD_SEL: 0,
            blob.ADDR_DATA_PLANE_SEL: 0,
        }
        self.cells = cells or [0] * depth
        self.ts_cells = ts_cells or [0] * depth
        self.writes = []

    async def read(self, addr):
        if addr in self.regs:
            return self.regs[addr]
        if blob.ADDR_DATA_BASE <= addr < blob.ADDR_DATA_BASE + 4 * self.depth:
            i = (addr - blob.ADDR_DATA_BASE) // 4
            plane = self.regs[blob.ADDR_DATA_PLANE_SEL]
            k = self.regs[blob.ADDR_DATA_WORD_SEL]
            src = self.cells if plane == 0 else self.ts_cells
            return (src[i] >> (32 * k)) & 0xFFFF_FFFF
        return 0

    async def write(self, addr, data):
        self.writes.append((addr, data))
        self.regs[addr] = data


def test_walker_builds_engine_ordered_blob_and_restores_selectors():
    fake = _FakeMap(depth=8, sample_w=40, ts_w=16, capture_len=5, start_ptr=6,
                    cells=[0xA5_0000_0000 | (0x100 + i) for i in range(8)],
                    ts_cells=[0x4000 + i for i in range(8)])
    got = asyncio.run(blob.read_window_blob(fake.read, fake.write))
    assert got["samples"] == [0xA5_0000_0106, 0xA5_0000_0107, 0xA5_0000_0100,
                              0xA5_0000_0101, 0xA5_0000_0102]
    assert got["timestamps"] == [0x4006, 0x4007, 0x4000, 0x4001, 0x4002]
    assert got["blob"] == blob.pack_window(got["samples"], 40, got["timestamps"], 16)
    assert got["start_ptr"] == 6 and got["capture_len"] == 5
    # both selectors are put back to 0 last (the REAClient contract)
    assert fake.writes[-2:] == [(blob.ADDR_DATA_WORD_SEL, 0), (blob.ADDR_DATA_PLANE_SEL, 0)]
    # word 1 was actually paged (a walker that never wrote DATA_WORD_SEL=1
    # would return the marker byte as 0)
    assert (blob.ADDR_DATA_WORD_SEL, 1) in fake.writes


def test_walker_refuses_before_done_and_on_feature_mismatch():
    fake = _FakeMap(depth=8, sample_w=8, ts_w=0, capture_len=1, start_ptr=0, done=False)
    with pytest.raises(RuntimeError, match="before done"):
        asyncio.run(blob.read_window_blob(fake.read, fake.write))
    fake = _FakeMap(depth=8, sample_w=8, ts_w=16, capture_len=1, start_ptr=0)
    fake.regs[blob.ADDR_FEATURES] = 0          # TIMESTAMP_W says plane, FEATURES[18] says none
    with pytest.raises(RuntimeError, match="FEATURES"):
        asyncio.run(blob.read_window_blob(fake.read, fake.write))


def test_window_transport_feature_bits_are_20_and_21():
    """REA-REQ-913: the advertised bit positions are part of the contract."""
    assert blob.FEAT_AXIS_WINDOW_BIT == 1 << 20
    assert blob.FEAT_UDP_WINDOW_BIT == 1 << 21
    assert blob.FEAT_TIMESTAMP_BIT == 1 << 18
