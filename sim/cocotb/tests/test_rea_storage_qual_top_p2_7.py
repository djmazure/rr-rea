# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.7 — storage qualification through rr_rea_top's JTAG door
(REA-REQ-956, REA-REQ-958).

The register map (QUAL_MODE/SEL/CFG/VAL, FEATURES[22]/[27:24], VERSION) and a
whole qualified capture — programmed over JTAG, read back through DATA_BASE on
both planes — with sparse events whose true sample-clock cycles the testbench
records independently of the DUT.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_SIM_ROOT = str(Path(__file__).resolve().parents[2])
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)

from test_rea_top import (  # noqa: E402
    _jtag_read,
    _jtag_write,
    _reset,
    _start_clocks,
)

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

GENERICS = {
    "G_SAMPLE_W": 12,
    "G_DEPTH": 32,
    "G_TIMESTAMP_W": 16,
    "G_NUM_CHAN": 1,
    "G_QUAL_CONDS": 2,
}
_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_VERSION = 0x00
ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_QUAL_MODE = 0xB4
ADDR_QUAL_SEL = 0xB8
ADDR_QUAL_CFG = 0xBC
ADDR_QUAL_VAL = 0xC0
ADDR_START_PTR = 0xC8
ADDR_DATA_WORD_SEL = 0xCC
ADDR_FEATURES = 0xD0
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_PRETRIG_VALID = 0xF0
ADDR_DATA_BASE = 0x100

OP_EQ = 0


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module=Path(__file__).stem,
        custom_libraries={
            "work": [
                str(_RTL_DIR / "rr_rea_pkg.vhd"),
                str(_FIX / "rr_rea_build_id_stub.vhd"),
                str(_RTL_DIR / "rr_rea_dpram.vhd"),
                str(_RTL_DIR / "rr_rea_capture_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_regbank.vhd"),
                str(_RTL_DIR / "rr_rea_cdc.vhd"),
                str(_RTL_DIR / "rr_rea_jtag_iface.vhd"),
                str(_RTL_DIR / "rr_rea_crc_sweep.vhd"),
                str(_RTL_DIR / "rr_rea_fill_fsm.vhd"),
                str(_RTL_DIR / "rr_rea_trust_core.vhd"),
                str(_RTL_DIR / "rr_rea_axis_window.vhd"),
                str(_RTL_DIR / "rr_rea_top.vhd"),
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


def _slot_cfg(op: int, lsb: int, width: int, valid: bool = True) -> int:
    """COND_CFG encoding: {valid[31], lsb_hi[30:28], op[27:24], width[23:16],
    lsb_lo[15:8]}."""
    return ((int(valid) << 31) | (((lsb >> 8) & 0x7) << 28) | ((op & 0xF) << 24)
            | ((width & 0xFF) << 16) | ((lsb & 0xFF) << 8))


@cocotb.test()
@requires("REA-REQ-958")
async def test_rea_req_958_qualifier_registers_features_and_version(dut):
    """QUAL_* read back on `rd_data_o` what was written; a slot page past
    G_QUAL_CONDS drops writes and reads 0; FEATURES[22]=1, [27:24]=2;
    VERSION = 0x5245410B."""
    await _start_clocks(dut)
    await _reset(dut)

    assert await _jtag_read(dut, ADDR_VERSION) == 0x5245410B
    features = await _jtag_read(dut, ADDR_FEATURES)
    assert (features >> 22) & 1 == 1, f"FEATURES=0x{features:08X}: [22] clear"
    assert (features >> 24) & 0xF == GENERICS["G_QUAL_CONDS"], (
        f"FEATURES[27:24]={(features >> 24) & 0xF}, want G_QUAL_CONDS")
    assert (features >> 23) & 1 == 0 and features >> 28 == 0, (
        f"FEATURES=0x{features:08X}: reserved bits set")

    # Reset values: qualification off.
    for addr in (ADDR_QUAL_MODE, ADDR_QUAL_SEL, ADDR_QUAL_CFG, ADDR_QUAL_VAL):
        assert await _jtag_read(dut, addr) == 0, f"0x{addr:02X} not reset to 0"

    await _jtag_write(dut, ADDR_QUAL_MODE, 0xFFFF_FFFF)
    assert await _jtag_read(dut, ADDR_QUAL_MODE) == 0x3, (
        "QUAL_MODE keeps only [1:0] {or, enable}")
    for slot, (cfg, val) in enumerate([(0x8123_4500, 0xDEAD_BEEF),
                                       (0x0456_7800, 0x0000_0042)]):
        await _jtag_write(dut, ADDR_QUAL_SEL, slot)
        await _jtag_write(dut, ADDR_QUAL_CFG, cfg)
        await _jtag_write(dut, ADDR_QUAL_VAL, val)
    for slot, (cfg, val) in enumerate([(0x8123_4500, 0xDEAD_BEEF),
                                       (0x0456_7800, 0x0000_0042)]):
        await _jtag_write(dut, ADDR_QUAL_SEL, slot)
        assert await _jtag_read(dut, ADDR_QUAL_SEL) == slot
        assert await _jtag_read(dut, ADDR_QUAL_CFG) == cfg
        assert await _jtag_read(dut, ADDR_QUAL_VAL) == val
    # A page past the array: write dropped, reads 0, no aliasing onto slot 0/1.
    await _jtag_write(dut, ADDR_QUAL_SEL, 5)
    await _jtag_write(dut, ADDR_QUAL_CFG, 0xFFFF_FFFF)
    await _jtag_write(dut, ADDR_QUAL_VAL, 0xFFFF_FFFF)
    assert await _jtag_read(dut, ADDR_QUAL_CFG) == 0
    assert await _jtag_read(dut, ADDR_QUAL_VAL) == 0
    await _jtag_write(dut, ADDR_QUAL_SEL, 0)
    assert await _jtag_read(dut, ADDR_QUAL_CFG) == 0x8123_4500
    assert await _jtag_read(dut, ADDR_QUAL_VAL) == 0xDEAD_BEEF


class EventDriver:
    """Sparse events on probe bit 0 (flag) with the event number in [11:1];
    junk with flag 0 between them. Records the sample-clock cycle of every
    event from the testbench's own counter — the oracle for the timestamps."""

    def __init__(self, dut, seed: int) -> None:
        self.dut = dut
        self.rng = random.Random(seed)
        self.cycle = 0
        self.event_cycle: dict = {}
        self.running = True

    async def run(self) -> None:
        n = 0
        while self.running:
            n += 1
            for _ in range(self.rng.randint(60, 140)):
                self.dut.probe_i.value = self.rng.getrandbits(12) & ~1
                await RisingEdge(self.dut.sample_clk_i)
                self.cycle += 1
            self.dut.probe_i.value = ((n << 1) | 1) & 0xFFF
            await ReadOnly()
            self.event_cycle[n] = self.cycle
            await RisingEdge(self.dut.sample_clk_i)
            self.cycle += 1


async def _read_plane(dut, start: int, plane: int, count: int) -> list:
    depth = GENERICS["G_DEPTH"]
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, plane)
    await _jtag_write(dut, ADDR_DATA_WORD_SEL, 0)
    out = []
    for i in range(count):
        out.append(await _jtag_read(dut, ADDR_DATA_BASE + 4 * ((start + i) % depth)))
    await _jtag_write(dut, ADDR_DATA_PLANE_SEL, 0)
    return out


@cocotb.test()
@requires("REA-REQ-956")
async def test_rea_req_956_qualified_capture_timestamps_are_event_times(dut):
    """Programmed over JTAG: qualify on the event flag, trigger on event 60.
    The sample plane holds exactly the events PRETRIG before .. POSTTRIG after
    the trigger event, and the
    timestamp plane holds the real gaps between them (mod 2**16), measured
    by the testbench's own cycle counter."""
    await _start_clocks(dut)
    await _reset(dut)
    drv = EventDriver(dut, 0x956)
    cocotb.start_soon(drv.run())

    pretrig, posttrig, trig = 6, 5, 60
    await _jtag_write(dut, ADDR_QUAL_SEL, 0)
    await _jtag_write(dut, ADDR_QUAL_CFG, _slot_cfg(OP_EQ, lsb=0, width=1))
    await _jtag_write(dut, ADDR_QUAL_VAL, 1)
    await _jtag_write(dut, ADDR_QUAL_MODE, 1)
    await _jtag_write(dut, ADDR_PRETRIG, pretrig)
    await _jtag_write(dut, ADDR_POSTTRIG, posttrig)
    await _jtag_write(dut, ADDR_TRIG_MODE, 1)
    await _jtag_write(dut, ADDR_TRIG_VALUE, ((trig << 1) | 1) & 0xFFF)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0xFFF)
    await _jtag_write(dut, ADDR_CTRL, 1)
    # Each JTAG write costs ~200 sample cycles; the arm must still land well
    # before the pre-trigger window starts, or the window is partly pre-arm.
    armed_by = max(drv.event_cycle, default=0)
    assert armed_by < trig - pretrig - 2, (
        f"arm landed after event {armed_by}; raise the trigger event")

    for _ in range(4000):
        if await _jtag_read(dut, ADDR_STATUS) & 0x04:
            break
        await ClockCycles(dut.tck_i, 8)
    else:
        raise AssertionError("qualified capture never completed")
    drv.running = False

    n = pretrig + posttrig + 1
    start = await _jtag_read(dut, ADDR_START_PTR) % GENERICS["G_DEPTH"]
    samples = [v & 0xFFF for v in await _read_plane(dut, start, 0, n)]
    stamps = [v & 0xFFFF for v in await _read_plane(dut, start, 1, n)]
    events = list(range(trig - pretrig, trig + posttrig + 1))
    assert samples == [((e << 1) | 1) & 0xFFF for e in events], (
        f"sample plane is not the events around the trigger: "
        f"{[hex(s) for s in samples]}")
    assert await _jtag_read(dut, ADDR_PRETRIG_VALID) == pretrig
    for (a, b), (ta, tb) in zip(zip(events, events[1:]), zip(stamps, stamps[1:])):
        want = (drv.event_cycle[b] - drv.event_cycle[a]) & 0xFFFF
        got = (tb - ta) & 0xFFFF
        assert got == want, (
            f"timestamp gap between events {a} and {b} is {got} cycles, the "
            f"testbench measured {want}")
    dut._log.info(f"qualified window spans "
                  f"{drv.event_cycle[events[-1]] - drv.event_cycle[events[0]]} "
                  f"cycles in {n} cells")


if __name__ == "__main__":
    main()
