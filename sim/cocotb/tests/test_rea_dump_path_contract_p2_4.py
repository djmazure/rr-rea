# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# REA-P2.4 — dump-path transport contract (docs/DUMP_PATH_STRATEGY.md,
# SPEC.md "Dump-path transports", REA-REQ-909..913; also covers REA-REQ-900
# and REA-REQ-902, which had no test).
#
# WHY. JTAG is the default door; AXI (and later a burst window / UDP) are
# TRUCKS for the window after STATUS.done — not a second analyser and not a
# second register map. The whole epic rests on one claim: the SPEC.md 32-bit
# register map is the protocol and a transport only moves addr/data or a
# window blob. This test pins that claim at the surface a host reaches: two
# real rr_rea_top cores, one behind the TAP (`jtag`), one behind
# rr_rea_axi4lite (`axi_lite`), same probe, same clock, same reset. Every
# named register must sit at the SAME offset and read the SAME value through
# both doors, and the window blob built from each door — the reference every
# window transport (REA-P2.5 AXI-Stream, REA-ICE.1 UDP) must reproduce — must
# be byte-identical.
#
# What would make this go red: an address offset or byte/word remap in either
# bridge, a bridge that returns the previous DATA cell (the registered-read
# lag, REA-REQ-904, which the AXI suite only proved on regbank registers and
# never on the DATA window), a paging rule that differs per door, a FEATURES
# bit that advertises a burst engine no core has, or a change to the window
# layout / rotation rule in rea_window_blob.py.

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

import rea_window_blob as blob  # noqa: E402

_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# 40-bit probe → ceil(40/32) = 2 DATA_WORD_SEL pages per cell, so the paging
# rule is exercised on BOTH doors (word 1 carries a constant marker byte);
# CAPTURE_LEN (16) < DEPTH (32) so the blob is a CAPTURE_LEN window starting
# at START_PTR, not the whole ring; a 16-bit timestamp plane so plane-major
# ordering and FEATURES[18] are both live.
GENERICS = {
    "G_SAMPLE_W": 40, "G_DEPTH": 32, "G_TIMESTAMP_W": 16,
    "G_NUM_CHAN": 1, "G_TRIG_CONDS": 1, "G_NUM_SOURCE": 1,
}
SAMPLE_W = GENERICS["G_SAMPLE_W"]
DEPTH = GENERICS["G_DEPTH"]
TIMESTAMP_W = GENERICS["G_TIMESTAMP_W"]
PRETRIG = 7
POSTTRIG = 8
CAPTURE_LEN = PRETRIG + POSTTRIG + 1

# Frozen register map (SPEC.md "SW Interface Contract"). Hard-coded, never
# read from rr_rea_pkg (ROUTERTL-002).
ADDR_VERSION        = 0x00
ADDR_CTRL           = 0x04
ADDR_STATUS         = 0x08
ADDR_SAMPLE_W       = 0x0C
ADDR_DEPTH          = 0x10
ADDR_PRETRIG        = 0x14
ADDR_POSTTRIG       = 0x18
ADDR_CAPTURE_LEN    = 0x1C
ADDR_TRIG_MODE      = 0x20
ADDR_TRIG_VALUE     = 0x24
ADDR_TRIG_MASK      = 0x28
ADDR_TIMESTAMP_W    = 0xC4
ADDR_START_PTR      = 0xC8
ADDR_DATA_WORD_SEL  = 0xCC
ADDR_FEATURES       = 0xD0
ADDR_DATA_PLANE_SEL = 0xD8
ADDR_DATA_BASE      = 0x100

EXPECTED_VERSION = 0x5245410F  # v0.15 feature tier (rr_rea_pkg C_REA_VERSION)
CTRL_BIT_ARM = 0x01
STATUS_BIT_DONE = 0x04

# Probe stimulus: [15:0] free-running counter, [16] = 1 (never-zero guard),
# [39:32] = 0xA5 marker so DATA_WORD_SEL=1 must return 0xA5 on both doors.
PROBE_MARKER = 0xA5 << 32
PROBE_GUARD = 1 << 16
TRIG_COUNT = 0x4000            # fires ~131 µs after reset — long after both arms
TRIG_VALUE = PROBE_GUARD | TRIG_COUNT
TRIG_MASK = 0x0001_FFFF

ACLK_PERIOD_NS = 10.0
SAMPLE_PERIOD_NS = 8.0
TCK_PERIOD_NS = 25.0


def main() -> None:
    run_simulation(
        top_level="rr_rea_dual_door_harness",
        module="test_rea_dump_path_contract_p2_4",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_FIX}/rr_rea_build_id_stub.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
                f"{_RTL_DIR}/rr_rea_crc_sweep.vhd",
                f"{_RTL_DIR}/rr_rea_fill_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_trust_core.vhd",
                f"{_RTL_DIR}/rr_rea_axi4lite.vhd",
                f"{_RTL_DIR}/rr_rea_axis_window.vhd",
                f"{_RTL_DIR}/rr_rea_top.vhd",
                f"{_FIX}/rr_rea_dual_door_harness.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Bring-up ─────────────────────────────────────────────────────────


async def _start(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.aclk_i, ACLK_PERIOD_NS, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    dut.aresetn_i.value = 0
    dut.probe_i.value = PROBE_MARKER | PROBE_GUARD
    # TAP idle
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    # jtag core's external slave ports idle (REA-REQ-902 drives them later)
    dut.j_reg_clk_i.value = 0
    dut.j_reg_wr_en_i.value = 0
    dut.j_reg_rd_en_i.value = 0
    dut.j_reg_addr_i.value = 0
    dut.j_reg_wdata_i.value = 0
    # AXI master idle
    dut.awvalid_i.value = 0
    dut.wvalid_i.value = 0
    dut.arvalid_i.value = 0
    dut.bready_i.value = 1
    dut.rready_i.value = 1
    dut.wstrb_i.value = 0xF
    dut.awaddr_i.value = 0
    dut.wdata_i.value = 0
    dut.araddr_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    dut.aresetn_i.value = 1
    await ClockCycles(dut.tck_i, 2)


async def _drive_probe_counter(dut):
    cnt = 0
    while True:
        dut.probe_i.value = PROBE_MARKER | PROBE_GUARD | (cnt & 0xFFFF)
        await RisingEdge(dut.sample_clk_i)
        cnt = (cnt + 1) & 0xFFFF


# ── Door 1: JTAG (same wire format as test_rea_top.py) ───────────────


async def _capture_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 1
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.update_i.value = 0
    dut.shift_en_i.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi_i.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo_o.value) & 1) << i
        await RisingEdge(dut.tck_i)
    dut.shift_en_i.value = 0
    return out


async def _update_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 1
    await RisingEdge(dut.tck_i)
    dut.update_i.value = 0
    dut.sel_i.value = 0


def _frame(addr: int, data: int, write: bool) -> int:
    return ((1 if write else 0) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)


async def _jtag_write(dut, addr: int, data: int) -> None:
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, data, write=True), 49)
    await _update_phase(dut)


async def _jtag_read(dut, addr: int) -> int:
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)
    await ClockCycles(dut.tck_i, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


# ── Door 2: AXI4-Lite (same BFM as test_rea_axi4lite_p3_931.py) ──────


async def _axi_write(dut, addr: int, data: int) -> None:
    dut.awaddr_i.value = addr
    dut.awvalid_i.value = 1
    dut.wdata_i.value = data
    dut.wvalid_i.value = 1
    while True:
        await RisingEdge(dut.aclk_i)
        if dut.awready_o.value == 1 and dut.wready_o.value == 1:
            break
    dut.awvalid_i.value = 0
    dut.wvalid_i.value = 0
    while True:
        await RisingEdge(dut.aclk_i)
        if dut.bvalid_o.value == 1 and dut.bready_i.value == 1:
            break
    assert int(dut.bresp_o.value) == 0, "write response must be OKAY"


async def _axi_read(dut, addr: int) -> int:
    dut.araddr_i.value = addr
    dut.arvalid_i.value = 1
    while True:
        await RisingEdge(dut.aclk_i)
        if dut.arready_o.value == 1:
            break
    dut.arvalid_i.value = 0
    while True:
        await RisingEdge(dut.aclk_i)
        if dut.rvalid_o.value == 1 and dut.rready_i.value == 1:
            break
    assert int(dut.rresp_o.value) == 0, "read response must be OKAY"
    return int(dut.rdata_o.value)


# The two doors as (name, read, write) verbs — the ONLY thing a register-map
# transport contributes.
def _doors(dut):
    return (
        ("jtag", lambda a: _jtag_read(dut, a), lambda a, d: _jtag_write(dut, a, d)),
        ("axi_lite", lambda a: _axi_read(dut, a), lambda a, d: _axi_write(dut, a, d)),
    )


async def _configure_and_arm(dut, write) -> None:
    await write(ADDR_PRETRIG, PRETRIG)
    await write(ADDR_POSTTRIG, POSTTRIG)
    await write(ADDR_TRIG_MODE, 0x1)          # value_match
    await write(ADDR_TRIG_VALUE, TRIG_VALUE)  # TRIG_WORD_SEL = 0 (reset)
    await write(ADDR_TRIG_MASK, TRIG_MASK)
    await write(ADDR_CTRL, CTRL_BIT_ARM)      # toggle: register resets to 0


async def _wait_done(dut, name, read) -> int:
    for _ in range(4000):
        status = await read(ADDR_STATUS)
        if status & STATUS_BIT_DONE:
            return status
        await ClockCycles(dut.sample_clk_i, 64)
    raise AssertionError(f"{name}: capture never reached STATUS.done")


# ── Tests ────────────────────────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-909")
async def test_rea_req_909_named_registers_identical_through_both_doors(dut):
    """VERSION / STATUS / DATA_WORD_SEL / DATA_BASE: same offset, same value,
    on the `jtag` door and the `axi_lite` door. The register map is the
    protocol; a transport does not get to move it."""
    await _start(dut)
    seen = {}
    for name, read, write in _doors(dut):
        version = await read(ADDR_VERSION)
        assert version == EXPECTED_VERSION, (
            f"{name}: VERSION at 0x{ADDR_VERSION:02X} = 0x{version:08X}, "
            f"expected 0x{EXPECTED_VERSION:08X} (REA-REQ-909)"
        )
        status = await read(ADDR_STATUS)
        assert status == 0, (
            f"{name}: idle STATUS at 0x{ADDR_STATUS:02X} = 0x{status:08X}, "
            "expected 0 before any arm — wrong register at that offset?"
        )
        # DATA_WORD_SEL: writable through the door, readable at the SAME
        # offset, and paging DATA_BASE (proved by the blob test below).
        await write(ADDR_DATA_WORD_SEL, 1)
        sel = await read(ADDR_DATA_WORD_SEL)
        assert sel == 1, (
            f"{name}: DATA_WORD_SEL at 0x{ADDR_DATA_WORD_SEL:02X} read back "
            f"{sel} after writing 1 (REA-REQ-909)"
        )
        await write(ADDR_DATA_WORD_SEL, 0)
        assert await read(ADDR_DATA_WORD_SEL) == 0
        # DATA_BASE is a window of DEPTH cells starting at 0x100 on both
        # doors: the first cell past the window reads zero, the last cell in
        # it decodes as a cell (never X). Content equality is the blob test.
        beyond = await read(ADDR_DATA_BASE + 4 * DEPTH)
        assert beyond == 0, (
            f"{name}: 0x{ADDR_DATA_BASE + 4 * DEPTH:04X} (first offset past "
            f"DATA_BASE+4*DEPTH) read 0x{beyond:08X}, expected 0 — the window "
            "is not where the map says (REA-REQ-909)"
        )
        seen[name] = {
            "sample_w": await read(ADDR_SAMPLE_W),
            "depth": await read(ADDR_DEPTH),
            "timestamp_w": await read(ADDR_TIMESTAMP_W),
            "features": await read(ADDR_FEATURES),
        }
    assert seen["jtag"] == seen["axi_lite"], (
        f"metadata differs by door: {seen} — one register map, two bridges "
        "(REA-REQ-909/900)"
    )
    assert seen["jtag"]["sample_w"] == SAMPLE_W and seen["jtag"]["depth"] == DEPTH
    dut._log.info("REA-REQ-909 PASS — VERSION/STATUS/DATA_WORD_SEL/DATA_BASE "
                  "identical through jtag and axi_lite")


@cocotb.test()
@requires("REA-REQ-911", "REA-REQ-912", "REA-REQ-900", "REA-REQ-904")
async def test_rea_req_911_912_900_window_blob_identical_from_both_doors(dut):
    """Both cores capture the same trigger; the window blob walked through
    each door is byte-identical, has the frozen layout (plane-major,
    ceil(SAMPLE_W/32) LE words, CAPTURE_LEN cells, timestamp plane because
    FEATURES[18]) and is engine-rotated: index PRETRIG is the trigger cell.
    Covers REA-REQ-911 (layout), REA-REQ-912 (rotation), REA-REQ-900
    (identical datapath behind either bridge) and REA-REQ-904 on the DATA
    window — the two-edge read latency the 1.1.0 bridge got wrong: it
    returned physical cell 0 for every DATA_BASE address over AXI while the
    JTAG door read the same capture correctly. This assertion is the one that
    was red on that RTL."""
    await _start(dut)
    cocotb.start_soon(_drive_probe_counter(dut))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    doors = _doors(dut)
    for name, read, write in doors:
        await _configure_and_arm(dut, write)
        assert await read(ADDR_CAPTURE_LEN) == CAPTURE_LEN, name

    results = {}
    for name, read, write in doors:
        status = await _wait_done(dut, name, read)
        results[name] = await blob.read_window_blob(read, write)
        results[name]["status"] = status

    j, a = results["jtag"], results["axi_lite"]

    # ── Layout (REA-REQ-911) ─────────────────────────────────────────
    expect_len = blob.blob_len(SAMPLE_W, CAPTURE_LEN, TIMESTAMP_W)
    assert expect_len == CAPTURE_LEN * (2 + 1) * 4, "test arithmetic"
    for name, r in results.items():
        assert r["features"] & blob.FEAT_TIMESTAMP_BIT, f"{name}: FEATURES[18] must be set"
        assert len(r["blob"]) == expect_len, (
            f"{name}: blob is {len(r['blob'])} bytes, layout says {expect_len} "
            f"(CAPTURE_LEN={CAPTURE_LEN} × (2 sample words + 1 ts word) × 4)"
        )
        samples, timestamps = blob.unpack_window(r["blob"], SAMPLE_W, CAPTURE_LEN, TIMESTAMP_W)
        assert samples == r["samples"] and timestamps == r["timestamps"], (
            f"{name}: pack/unpack round-trip broke — layout is not self-consistent"
        )
        # word 1 of every cell is the marker byte: DATA_WORD_SEL=1 paged the
        # high bits, zero-padded, through THIS door.
        for i, cell in enumerate(samples):
            assert cell >> 32 == 0xA5, (
                f"{name}: cell {i} word 1 = 0x{cell >> 32:02X}, expected the 0xA5 "
                "marker — DATA_WORD_SEL paging differs on this door (REA-REQ-911)"
            )
            assert cell & PROBE_GUARD, f"{name}: cell {i} = 0x{cell:010X} lost bit 16"
        # plane-major: the sample plane occupies the first CAPTURE_LEN*2 words
        # of the blob, the timestamp plane the rest.
        first_ts_word = int.from_bytes(r["blob"][CAPTURE_LEN * 8: CAPTURE_LEN * 8 + 4], "little")
        assert first_ts_word == timestamps[0], f"{name}: timestamp plane not after sample plane"

    # ── Rotation choice (REA-REQ-912): engine order, trigger at PRETRIG ──
    for name, r in results.items():
        samples, timestamps = r["samples"], r["timestamps"]
        dut._log.info(
            f"{name}: START_PTR={r['start_ptr']} STATUS=0x{r['status']:02X} "
            f"samples={[hex(x & 0x1FFFF) for x in samples]} ts={timestamps}"
        )
        trig = samples[PRETRIG] & 0x1FFFF
        assert trig == TRIG_VALUE, (
            f"{name}: blob[{PRETRIG}] = 0x{trig:05X}, expected the trigger value "
            f"0x{TRIG_VALUE:05X} — cell i must be physical (START_PTR+i) mod DEPTH "
            "so a window transport needs no host rotation (REA-REQ-912)"
        )
        for i in range(1, CAPTURE_LEN):
            assert (samples[i] - samples[i - 1]) & 0xFFFF == 1, (
                f"{name}: sample plane not time-monotonic at blob index {i}"
            )
            assert (timestamps[i] - timestamps[i - 1]) & 0xFFFF == 1, (
                f"{name}: timestamp plane not time-monotonic at blob index {i}"
            )

    # ── One datapath, two bridges (REA-REQ-900): byte-identical blobs ─
    assert j["capture_len"] == a["capture_len"] == CAPTURE_LEN
    assert j["blob"] == a["blob"], (
        "window blob differs between the jtag and axi_lite doors:\n"
        f"  jtag     samples={[hex(s) for s in j['samples']]}\n"
        f"  axi_lite samples={[hex(s) for s in a['samples']]}\n"
        f"  jtag     ts={j['timestamps']}\n  axi_lite ts={a['timestamps']}\n"
        f"  start_ptr jtag={j['start_ptr']} axi_lite={a['start_ptr']}"
    )
    dut._log.info(
        f"REA-REQ-911/912/900 PASS — {len(j['blob'])}-byte blob identical from both "
        f"doors; trigger at index {PRETRIG}; START_PTR jtag={j['start_ptr']} "
        f"axi_lite={a['start_ptr']}"
    )


@cocotb.test()
@requires("REA-REQ-913")
async def test_rea_req_913_no_window_engine_is_advertised(dut):
    """FEATURES[20] (axi_stream_window) and [21] (udp_window) read 0 on a core
    that elaborates no window-dump engine — through both doors. A host may
    pick a window transport only when the bit is set; today it never is."""
    await _start(dut)
    for name, read, _ in _doors(dut):
        features = await read(ADDR_FEATURES)
        assert not features & blob.FEAT_AXIS_WINDOW_BIT, (
            f"{name}: FEATURES[20] set (0x{features:08X}) but this core has no "
            "AXI-Stream window-dump engine (REA-REQ-913)"
        )
        assert not features & blob.FEAT_UDP_WINDOW_BIT, (
            f"{name}: FEATURES[21] set (0x{features:08X}) but udp_window is Icebox "
            "(REA-REQ-913)"
        )
        assert features & blob.FEAT_TIMESTAMP_BIT, f"{name}: FEATURES[18] must be set here"
    dut._log.info("REA-REQ-913 PASS — no window transport advertised")


@cocotb.test()
@requires("REA-REQ-902", "REA-REQ-910")
async def test_rea_req_902_910_jtag_core_has_no_second_door(dut):
    """The `jtag` core's reg_*_i slave ports are wired AND driven here with a
    well-formed register write — and must move nothing. One core, one door:
    the external port is not a back door onto a JTAG core, and dual-master
    stays forbidden until the arbiter ticket (REA-REQ-910 / REA-REQ-901)."""
    await _start(dut)
    await _jtag_write(dut, ADDR_PRETRIG, 0x5A)
    assert await _jtag_read(dut, ADDR_PRETRIG) == 0x5A

    # A complete registered-bus write on the jtag core's external port.
    dut.j_reg_addr_i.value = ADDR_PRETRIG
    dut.j_reg_wdata_i.value = 0x33
    dut.j_reg_wr_en_i.value = 1
    for _ in range(4):
        dut.j_reg_clk_i.value = 1
        await ClockCycles(dut.aclk_i, 1)
        dut.j_reg_clk_i.value = 0
        await ClockCycles(dut.aclk_i, 1)
    dut.j_reg_wr_en_i.value = 0
    dut.j_reg_rd_en_i.value = 1
    for _ in range(4):
        dut.j_reg_clk_i.value = 1
        await ClockCycles(dut.aclk_i, 1)
        dut.j_reg_clk_i.value = 0
        await ClockCycles(dut.aclk_i, 1)
    dut.j_reg_rd_en_i.value = 0
    await ClockCycles(dut.tck_i, 4)

    after = await _jtag_read(dut, ADDR_PRETRIG)
    assert after == 0x5A, (
        f"PRETRIG moved 0x5A -> 0x{after:02X} after a write on the jtag core's "
        "reg_*_i ports — a jtag core must have exactly one door (REA-REQ-902/910)"
    )
    dut._log.info("REA-REQ-902/910 PASS — external port inert on the jtag core")


if __name__ == "__main__":
    main()
