# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# Tests cover REA-REQ-801 / 805 (see ../../../requirements.yml). Run via:
#   rr sim run test_rea_crc_sweep

from __future__ import annotations

import sys as _sys
import zlib
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parents[1])  # sim/cocotb
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

_RTL = str(_Path(__file__).resolve().parents[3] / "rtl")

_BUILDS = {
    # hex(zlib.crc32(b"".join(((cell_value(i, 32) >> (32*k)) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(8) for k in range(1))))
    32: (8, 0xD1074AF3),
    # hex(zlib.crc32(b"".join(((cell_value(i, 33) >> (32*k)) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(4) for k in range(2))))
    33: (4, 0x7A2F5767),
    # hex(zlib.crc32(b"".join(((cell_value(i, 64) >> (32*k)) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(4) for k in range(2))))
    64: (4, 0xF857A345),
    # hex(zlib.crc32(b"".join(((cell_value(i, 704) >> (32*k)) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(2) for k in range(22))))
    704: (2, 0xFD87AFB8),
}


def cell_value(i: int, width: int) -> int:
    nbytes = (width + 7) // 8
    value = 0
    for j in range(nbytes):
        value |= ((0x11 * (i + 1) + j) & 0xFF) << (8 * j)
    return value & ((1 << width) - 1)


async def drive_memory(dut, width: int) -> None:
    while True:
        await RisingEdge(dut.sample_clk_i)
        if dut.mem_rd_en_o.value == 1:
            address = int(dut.mem_addr_o.value)
            dut.mem_dout_i.value = cell_value(address, width)


def canonical_page_bytes(width: int, depth: int) -> bytes:
    return b"".join(
        ((cell_value(i, width) >> (32 * k)) & 0xFFFFFFFF).to_bytes(4, "little")
        for i in range(depth)
        for k in range((width + 31) // 32)
    )


@cocotb.test()
@requires("REA-REQ-801", "REA-REQ-805")
async def test_crc_sweep(dut) -> None:
    width = len(dut.mem_dout_i)
    depth, golden = _BUILDS[width]

    assert zlib.crc32(canonical_page_bytes(width, depth)) == golden

    dut.sample_rst_i.value = 1
    dut.start_i.value = 0
    dut.mem_dout_i.value = 0
    cocotb.start_soon(Clock(dut.sample_clk_i, 10, unit="ns").start())
    cocotb.start_soon(drive_memory(dut, width))

    await RisingEdge(dut.sample_clk_i)
    await RisingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    await RisingEdge(dut.sample_clk_i)

    # Single-cycle start pulse, driven relative to the rising edge (the DUT
    # samples on rising_edge; no falling-edge stimulus — REA is edge-clean).
    dut.start_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.start_i.value = 0
    await ReadOnly()
    assert dut.busy_o.value == 1

    max_cycles = depth * (4 * ((width + 31) // 32) + 4) + 10
    for _ in range(max_cycles):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.crc_done_o.value):
            break
    else:
        raise AssertionError("crc_done did not assert")

    assert int(dut.crc_o.value) == golden
    assert dut.busy_o.value == 0

    await RisingEdge(dut.sample_clk_i)
    await ReadOnly()
    assert dut.crc_done_o.value == 0
    assert int(dut.crc_o.value) == golden


def main() -> None:
    for width, (depth, _) in _BUILDS.items():
        run_simulation(
            top_level="rr_rea_crc_sweep",
            module=_Path(__file__).stem,
            custom_libraries={
                "work": [
                    f"{_RTL}/rr_rea_pkg.vhd",
                    f"{_RTL}/rr_rea_crc_sweep.vhd",
                ],
            },
            generics={"G_SAMPLE_W": width, "G_DEPTH": depth},
            simulator="nvc",
        )


if __name__ == "__main__":
    main()
