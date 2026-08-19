# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA window blob — the ONE definition every dump transport must reproduce.

REA-P2.4 (docs/DUMP_PATH_STRATEGY.md, SPEC.md "Dump-path transports",
REA-REQ-911/912). Pure Python, no cocotb import: usable from a cocotb test
(pass it async register read/write callables), from a pytest structural test,
and — the reason it is a module and not a helper buried in one test file —
from the REA-P2.5 AXI-Stream window-dump test as the comparison oracle. A
green burst dump that never compared against DATA_BASE is not done; THIS is
what it compares against.

Layout (frozen by REA-REQ-911):

  * headerless payload — every piece of metadata (SAMPLE_W, TIMESTAMP_W,
    CAPTURE_LEN, START_PTR, FEATURES) comes from the register map, which is
    the protocol; a transport only moves this blob;
  * plane-major: the whole sample plane, then the whole timestamp plane
    (present iff FEATURES[18]);
  * each plane holds CAPTURE_LEN cells; cell i is ceil(plane_w/32) 32-bit
    little-endian words, word k = plane bits [32k+31 : 32k], the final partial
    word zero-padded — byte-for-byte the value a host reads at DATA_BASE with
    DATA_WORD_SEL = k, so the same paging rule serves both paths;
  * cell order is ENGINE-ROTATED, trigger-at-pretrig (REA-REQ-912): cell i is
    physical DATA_BASE cell (START_PTR + i) mod DEPTH, so index PRETRIG is
    the trigger sample and no host-side rotation is needed for a window
    transport. Register-map transports (jtag / axi_lite) keep host-side
    rotation: they walk DATA_BASE by physical address and REAClient rotates.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Sequence

WORD_BITS = 32
WORD_BYTES = 4

# Frozen register offsets this walker touches (SPEC.md "SW Interface
# Contract"). Hard-coded here on purpose (ROUTERTL-002): a walker that took
# its addresses from the DUT's own package could not notice a moved register.
ADDR_STATUS         = 0x08
ADDR_SAMPLE_W       = 0x0C
ADDR_DEPTH          = 0x10
ADDR_CAPTURE_LEN    = 0x1C
ADDR_TIMESTAMP_W    = 0xC4
ADDR_START_PTR      = 0xC8
ADDR_DATA_WORD_SEL  = 0xCC
ADDR_FEATURES       = 0xD0
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE      = 0x100

STATUS_DONE_BIT      = 1 << 2
FEAT_TIMESTAMP_BIT   = 1 << 18
# REA-REQ-913: FEATURES[20] advertises an elaborated axi_stream_window dump
# engine; [21] is reserved for udp_window. Both read 0 on a core without one.
FEAT_AXIS_WINDOW_BIT = 1 << 20
FEAT_UDP_WINDOW_BIT  = 1 << 21

PLANE_SAMPLE    = 0
PLANE_TIMESTAMP = 1


def words_per_cell(plane_w: int) -> int:
    """ceil(plane_w / 32) — the DATA_WORD_SEL page count of one cell."""
    if plane_w <= 0:
        raise ValueError(f"plane width must be positive, got {plane_w}")
    return (plane_w + WORD_BITS - 1) // WORD_BITS


def cell_words(value: int, plane_w: int) -> list[int]:
    """Split one cell into its little-endian 32-bit pages, zero-padded."""
    mask = (1 << plane_w) - 1
    value &= mask
    return [(value >> (WORD_BITS * k)) & 0xFFFF_FFFF
            for k in range(words_per_cell(plane_w))]


def rotate(physical: Sequence[int], start_ptr: int, capture_len: int,
           depth: int) -> list[int]:
    """Engine order: cell i = physical[(start_ptr + i) mod depth]."""
    if not 0 <= start_ptr < depth:
        raise ValueError(f"START_PTR {start_ptr} outside ring of {depth}")
    if not 0 < capture_len <= depth:
        raise ValueError(f"CAPTURE_LEN {capture_len} outside 1..{depth}")
    if len(physical) != depth:
        raise ValueError(f"need all {depth} physical cells, got {len(physical)}")
    return [physical[(start_ptr + i) % depth] for i in range(capture_len)]


def pack_window(samples: Sequence[int], sample_w: int,
                timestamps: Sequence[int] | None = None,
                timestamp_w: int = 0) -> bytes:
    """Serialise an engine-ordered window into the frozen blob layout.

    `samples` (and `timestamps`, when the plane exists) are already in engine
    order — index 0 is the oldest cell, len(...) == CAPTURE_LEN.
    """
    out = bytearray()
    for cell in samples:
        for word in cell_words(cell, sample_w):
            out += word.to_bytes(WORD_BYTES, "little")
    if timestamps is not None:
        if timestamp_w <= 0:
            raise ValueError("timestamp plane given but TIMESTAMP_W is 0")
        if len(timestamps) != len(samples):
            raise ValueError("timestamp plane must have CAPTURE_LEN cells too")
        for cell in timestamps:
            for word in cell_words(cell, timestamp_w):
                out += word.to_bytes(WORD_BYTES, "little")
    return bytes(out)


def blob_len(sample_w: int, capture_len: int, timestamp_w: int = 0) -> int:
    n = capture_len * words_per_cell(sample_w) * WORD_BYTES
    if timestamp_w > 0:
        n += capture_len * words_per_cell(timestamp_w) * WORD_BYTES
    return n


def unpack_window(blob: bytes, sample_w: int, capture_len: int,
                  timestamp_w: int = 0) -> tuple[list[int], list[int] | None]:
    """Inverse of pack_window; raises on a blob of the wrong length."""
    expect = blob_len(sample_w, capture_len, timestamp_w)
    if len(blob) != expect:
        raise ValueError(f"blob is {len(blob)} bytes, layout says {expect}")
    pos = 0

    def _plane(plane_w: int) -> list[int]:
        nonlocal pos
        cells = []
        n_words = words_per_cell(plane_w)
        for _ in range(capture_len):
            value = 0
            for k in range(n_words):
                value |= int.from_bytes(blob[pos:pos + WORD_BYTES], "little") << (WORD_BITS * k)
                pos += WORD_BYTES
            cells.append(value & ((1 << plane_w) - 1))
        return cells

    samples = _plane(sample_w)
    timestamps = _plane(timestamp_w) if timestamp_w > 0 else None
    return samples, timestamps


ReadFn = Callable[[int], Awaitable[int]]
WriteFn = Callable[[int, int], Awaitable[None]]


async def read_window_blob(read: ReadFn, write: WriteFn) -> dict:
    """Walk the register map exactly as a host does and build the blob.

    Transport-agnostic: `read(addr) -> int` and `write(addr, data)` are the
    only two verbs a register-map transport has (jtag, axi_lite). Returns a
    dict with the blob and every metadata word it was built from, so a test
    can compare two doors field by field and not just blob to blob.

    Reads word-major (one DATA_WORD_SEL setting per pass over the window,
    the cheap order on a scan chain) but ASSEMBLES cell-major — the blob
    layout is independent of the read order that produced it.
    """
    status = await read(ADDR_STATUS)
    if not status & STATUS_DONE_BIT:
        raise RuntimeError(f"STATUS=0x{status:08X}: window read before done")
    sample_w    = await read(ADDR_SAMPLE_W)
    depth       = await read(ADDR_DEPTH)
    capture_len = await read(ADDR_CAPTURE_LEN)
    timestamp_w = await read(ADDR_TIMESTAMP_W)
    start_ptr   = await read(ADDR_START_PTR) % depth
    features    = await read(ADDR_FEATURES)
    has_ts = bool(features & FEAT_TIMESTAMP_BIT)
    if has_ts != (timestamp_w > 0):
        raise RuntimeError(
            f"FEATURES[18]={int(has_ts)} disagrees with TIMESTAMP_W={timestamp_w}")

    async def _plane(plane: int, plane_w: int) -> list[int]:
        await write(ADDR_DATA_PLANE_SEL, plane)
        cells = [0] * capture_len
        for k in range(words_per_cell(plane_w)):
            await write(ADDR_DATA_WORD_SEL, k)
            for i in range(capture_len):
                phys = (start_ptr + i) % depth
                word = await read(ADDR_DATA_BASE + WORD_BYTES * phys)
                cells[i] |= (word & 0xFFFF_FFFF) << (WORD_BITS * k)
        return [c & ((1 << plane_w) - 1) for c in cells]

    samples = await _plane(PLANE_SAMPLE, sample_w)
    timestamps = await _plane(PLANE_TIMESTAMP, timestamp_w) if has_ts else None
    # Restore the selectors — the REAClient contract leaves both at 0.
    await write(ADDR_DATA_WORD_SEL, 0)
    await write(ADDR_DATA_PLANE_SEL, 0)

    return {
        "blob": pack_window(samples, sample_w, timestamps, timestamp_w),
        "samples": samples,
        "timestamps": timestamps,
        "sample_w": sample_w,
        "depth": depth,
        "capture_len": capture_len,
        "timestamp_w": timestamp_w,
        "start_ptr": start_ptr,
        "features": features,
    }
