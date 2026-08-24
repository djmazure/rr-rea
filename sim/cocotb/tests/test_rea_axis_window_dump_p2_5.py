# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# REA-P2.5 — AXI-Stream window dump of the capture after STATUS.done
# (docs/DUMP_PATH_STRATEGY.md, SPEC.md "Dump-path transports", REA-REQ-911/912/
# 913/914).
#
# WHY. axi_stream_window is the first burst "truck" for the window: after
# STATUS.done an AXIS master emits the packed window blob, control staying on
# the register map. The whole epic rests on ONE claim — the burst carries the
# SAME bytes a host reads by walking DATA_BASE, in the SAME frozen layout, so a
# window consumer needs no host rotation. A green dump that never compared
# against DATA_BASE is not done (the hostile check the ticket names), so this
# test reads the SAME physical capture two ways from ONE real rr_rea_top:
#
#   * walked cell-by-cell over the AXI4-Lite register door — blob.read_window_blob,
#     the DATA_BASE oracle rea_window_blob.py freezes; and
#   * streamed as one AXIS burst by the elaborated engine —
#
# and asserts they are BYTE-IDENTICAL. It further pins the beat count against
# the layout formula, proves tlast lands on exactly the last beat, and proves
# the master HOLDS tdata/tlast under back-pressure (tvalid high, tready low).
#
# What makes it go red: a byte/word/plane remap in the engine, a wrong rotation
# (blob[PRETRIG] not the trigger), a missing or misplaced tlast, a beat too many
# or too few, a torn cell under back-pressure, or FEATURES[20] not tracking the
# elaborated engine.

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

import rea_window_blob as blob  # noqa: E402

_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# 40-bit probe → 2 DATA_WORD_SEL pages/cell (the paging rule is live), 16-bit
# timestamp plane → the burst carries BOTH planes, plane-major, with different
# words-per-cell per plane. CAPTURE_LEN (16) < DEPTH (32) so the window is a
# rotated slice of the ring, not the whole buffer.
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

# Frozen register map (SPEC.md "SW Interface Contract"). Hard-coded (ROUTERTL-002).
ADDR_CTRL           = 0x04
ADDR_STATUS         = 0x08
ADDR_PRETRIG        = 0x14
ADDR_POSTTRIG       = 0x18
ADDR_CAPTURE_LEN    = 0x1C
ADDR_TRIG_MODE      = 0x20
ADDR_TRIG_VALUE     = 0x24
ADDR_TRIG_MASK      = 0x28
ADDR_FEATURES       = 0xD0
ADDR_DATA_BASE      = 0x100

CTRL_BIT_ARM = 0x01
STATUS_BIT_DONE = 0x04

PROBE_MARKER = 0xA5 << 32
PROBE_GUARD = 1 << 16
# START_PTR = (TRIG_COUNT - 0x4000) mod DEPTH for this bring-up alignment. 0x4019
# lands START_PTR at 25 so the CAPTURE_LEN=16 window WRAPS the 32-cell ring
# (25..31, 0..8) — the engine's (START_PTR + i) mod DEPTH rotation is genuinely
# exercised, not the START_PTR=0 identity case (see the non-zero assertion below).
TRIG_COUNT = 0x4019
TRIG_VALUE = PROBE_GUARD | TRIG_COUNT
TRIG_MASK = 0x0001_FFFF

ACLK_PERIOD_NS = 10.0
SAMPLE_PERIOD_NS = 8.0


def main() -> None:
    run_simulation(
        top_level="rr_rea_axis_dump_harness",
        module="test_rea_axis_window_dump_p2_5",
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
                f"{_FIX}/rr_rea_axis_dump_harness.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Bring-up ─────────────────────────────────────────────────────────


async def _start(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.aclk_i, ACLK_PERIOD_NS, unit="ns").start())
    dut.sample_rst_i.value = 1
    dut.aresetn_i.value = 0
    dut.probe_i.value = PROBE_MARKER | PROBE_GUARD
    dut.awvalid_i.value = 0
    dut.wvalid_i.value = 0
    dut.arvalid_i.value = 0
    dut.bready_i.value = 1
    dut.rready_i.value = 1
    dut.wstrb_i.value = 0xF
    dut.awaddr_i.value = 0
    dut.wdata_i.value = 0
    dut.araddr_i.value = 0
    dut.m_axis_tready_i.value = 0
    await ClockCycles(dut.aclk_i, 4)
    dut.sample_rst_i.value = 0
    dut.aresetn_i.value = 1
    await ClockCycles(dut.aclk_i, 2)


async def _drive_probe_counter(dut):
    cnt = 0
    while True:
        dut.probe_i.value = PROBE_MARKER | PROBE_GUARD | (cnt & 0xFFFF)
        await RisingEdge(dut.sample_clk_i)
        cnt = (cnt + 1) & 0xFFFF


# ── AXI4-Lite BFM (same as test_rea_dump_path_contract_p2_4) ─────────


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


async def _configure_and_arm(dut) -> None:
    await _axi_write(dut, ADDR_PRETRIG, PRETRIG)
    await _axi_write(dut, ADDR_POSTTRIG, POSTTRIG)
    await _axi_write(dut, ADDR_TRIG_MODE, 0x1)          # value_match
    await _axi_write(dut, ADDR_TRIG_VALUE, TRIG_VALUE)
    await _axi_write(dut, ADDR_TRIG_MASK, TRIG_MASK)
    await _axi_write(dut, ADDR_CTRL, CTRL_BIT_ARM)


async def _wait_done(dut) -> int:
    for _ in range(4000):
        status = await _axi_read(dut, ADDR_STATUS)
        if status & STATUS_BIT_DONE:
            return status
        await ClockCycles(dut.sample_clk_i, 64)
    raise AssertionError("capture never reached STATUS.done")


# ── AXIS burst collector ─────────────────────────────────────────────


class _AxisSink:
    """Collect the AXIS burst on aclk, exercising back-pressure and proving the
    master holds tdata/tlast stable while tvalid=1 and tready=0."""

    def __init__(self, dut):
        self.dut = dut
        self.beats: list[int] = []        # accepted tdata words, in order
        self.last_index = -1              # beat index that carried tlast
        self.tlast_count = 0
        self.done = False

    async def run(self):
        dut = self.dut
        # Back-pressure pattern: stall for 2 cycles, accept for 3, repeating.
        pattern = [0, 0, 1, 1, 1]
        k = 0
        dut.m_axis_tready_i.value = pattern[0]
        held = None
        while not self.done:
            await ReadOnly()
            tvalid = int(dut.m_axis_tvalid_o.value)
            tready = int(dut.m_axis_tready_i.value)
            tdata = int(dut.m_axis_tdata_o.value)
            tlast = int(dut.m_axis_tlast_o.value)
            if tvalid and not tready:
                # Stalled beat: the payload MUST NOT change while we hold off.
                if held is not None:
                    assert (tdata, tlast) == held, (
                        f"AXIS master changed payload under back-pressure: "
                        f"{held} -> {(tdata, tlast)} (tvalid high, tready low)"
                    )
                held = (tdata, tlast)
            elif tvalid and tready:
                self.beats.append(tdata)
                if tlast:
                    self.tlast_count += 1
                    self.last_index = len(self.beats) - 1
                    self.done = True
                held = None
            else:
                held = None
            await RisingEdge(dut.aclk_i)
            k += 1
            dut.m_axis_tready_i.value = pattern[k % len(pattern)]
        dut.m_axis_tready_i.value = 1


# ── Test ─────────────────────────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-914", "REA-REQ-911", "REA-REQ-912", "REA-REQ-913")
async def test_axis_burst_is_byte_identical_to_data_base_walk(dut):
    await _start(dut)

    # FEATURES[20] advertises the elaborated engine; [21] (udp) stays reserved.
    features = await _axi_read(dut, ADDR_FEATURES)
    assert features & blob.FEAT_AXIS_WINDOW_BIT, (
        f"FEATURES=0x{features:08X}: bit 20 (axi_stream_window) must be set on a "
        "core built with G_AXIS_WINDOW=true (REA-REQ-913)"
    )
    assert not features & blob.FEAT_UDP_WINDOW_BIT, "FEATURES[21] is reserved 0"
    assert features & blob.FEAT_TIMESTAMP_BIT, "FEATURES[18] must be set here"

    cocotb.start_soon(_drive_probe_counter(dut))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    # Arm the collector BEFORE the trigger so no burst beat is missed.
    sink = _AxisSink(dut)
    cocotb.start_soon(sink.run())

    await _configure_and_arm(dut)
    assert await _axi_read(dut, ADDR_CAPTURE_LEN) == CAPTURE_LEN
    await _wait_done(dut)

    # Let the whole burst drain.
    for _ in range(20000):
        if sink.done:
            break
        await ClockCycles(dut.aclk_i, 1)
    assert sink.done, "AXIS burst never asserted tlast"

    axis_bytes = b"".join(w.to_bytes(4, "little") for w in sink.beats)

    # ── Oracle: walk DATA_BASE over the register door (engine now idle) ──
    oracle = await blob.read_window_blob(
        lambda a: _axi_read(dut, a), lambda a, d: _axi_write(dut, a, d))

    expect_len = blob.blob_len(SAMPLE_W, CAPTURE_LEN, TIMESTAMP_W)
    expect_beats = expect_len // 4

    # The rotation requirement (REA-REQ-912) is only EXERCISED when the window
    # actually wraps the ring — a START_PTR of 0 makes engine order and physical
    # order identical, so a broken (un-rotated) engine would still match. Assert
    # the capture geometry gives a non-zero START_PTR so this test can catch a
    # rotation bug; the pre-arm delay below is tuned to land there deterministically.
    assert oracle["start_ptr"] != 0 and oracle["start_ptr"] + CAPTURE_LEN > DEPTH, (
        f"START_PTR={oracle['start_ptr']}: window does not wrap the ring, so the "
        "(START_PTR + i) mod DEPTH rotation is not exercised — adjust TRIG_COUNT"
    )

    # ── Beat count = CAPTURE_LEN * (ceil(SAMPLE_W/32) + ceil(TS_W/32)) ──
    assert expect_beats == CAPTURE_LEN * (2 + 1), "test arithmetic"
    assert len(sink.beats) == expect_beats, (
        f"AXIS emitted {len(sink.beats)} beats, layout wants {expect_beats} "
        f"(CAPTURE_LEN={CAPTURE_LEN} * (2 sample + 1 ts words))"
    )

    # ── tlast on exactly the last beat, once ─────────────────────────
    assert sink.tlast_count == 1, f"tlast asserted {sink.tlast_count} times, want 1"
    assert sink.last_index == expect_beats - 1, (
        f"tlast on beat {sink.last_index}, want the final beat {expect_beats - 1}"
    )

    # ── The burst IS the DATA_BASE blob, byte for byte ───────────────
    assert axis_bytes == oracle["blob"], (
        "AXIS burst differs from the DATA_BASE walk of the same capture:\n"
        f"  axis  ={axis_bytes.hex()}\n"
        f"  oracle={oracle['blob'].hex()}"
    )

    # ── And it decodes as the frozen layout, engine-rotated ──────────
    samples, timestamps = blob.unpack_window(axis_bytes, SAMPLE_W, CAPTURE_LEN, TIMESTAMP_W)
    assert samples == oracle["samples"] and timestamps == oracle["timestamps"]
    trig = samples[PRETRIG] & 0x1FFFF
    assert trig == TRIG_VALUE, (
        f"streamed blob[{PRETRIG}] = 0x{trig:05X}, want trigger 0x{TRIG_VALUE:05X} "
        "— engine rotation must put the trigger at index PRETRIG (REA-REQ-912)"
    )
    for i, cell in enumerate(samples):
        assert cell >> 32 == 0xA5, f"cell {i} lost the 0xA5 marker word"
        if i:
            assert (samples[i] - samples[i - 1]) & 0xFFFF == 1, "samples not monotonic"
            assert (timestamps[i] - timestamps[i - 1]) & 0xFFFF == 1, "ts not monotonic"

    dut._log.info(
        f"REA-P2.5 PASS — {len(sink.beats)}-beat AXIS burst byte-identical to the "
        f"DATA_BASE walk; START_PTR={oracle['start_ptr']} (window wraps the ring); "
        f"tlast on beat {sink.last_index}; trigger at index {PRETRIG}"
    )


@cocotb.test()
@requires("REA-REQ-916")
async def test_concurrent_data_base_reads_do_not_corrupt_burst(dut):
    # REA-REQ-916: the dump engine and the DATA_BASE decode share DPRAM port B
    # through a local arbiter, and the engine keeps port B for the whole burst.
    # A host DATA_BASE read during the dump gets an UNDEFINED value (the cell the
    # engine is mid-walk on) — DATA_BASE is undefined until tlast — but it must
    # NOT steal the port and corrupt the engine's own read. This test hammers
    # DATA_BASE over the AXI4-Lite door THROUGHOUT the burst and asserts the
    # collected burst is still byte-identical to a post-dump DATA_BASE walk.
    #
    # FALSIFIABILITY: if the arbiter were "fixed" the wrong way — giving the
    # register decode priority so an overlapping read wins port B — the engine's
    # mid-cell read would be stolen and the burst would tear, turning this red.
    await _start(dut)
    cocotb.start_soon(_drive_probe_counter(dut))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    sink = _AxisSink(dut)
    cocotb.start_soon(sink.run())

    await _configure_and_arm(dut)
    await _wait_done(dut)

    # Hammer DATA_BASE over the control door for the whole burst. Reads return an
    # undefined (engine-owned) value with an OKAY response; we only care that the
    # concurrent traffic does not disturb the stream. Walk several ring cells so
    # the read address genuinely moves under the engine.
    async def _hammer():
        i = 0
        while not sink.done:
            await _axi_read(dut, ADDR_DATA_BASE + 4 * (i % DEPTH))
            i += 1

    cocotb.start_soon(_hammer())

    for _ in range(40000):
        if sink.done:
            break
        await ClockCycles(dut.aclk_i, 1)
    assert sink.done, "AXIS burst never asserted tlast under concurrent DATA_BASE reads"

    axis_bytes = b"".join(w.to_bytes(4, "little") for w in sink.beats)

    # Engine idle now — walk DATA_BASE for the oracle (reads are defined again).
    oracle = await blob.read_window_blob(
        lambda a: _axi_read(dut, a), lambda a, d: _axi_write(dut, a, d))

    assert oracle["start_ptr"] != 0 and oracle["start_ptr"] + CAPTURE_LEN > DEPTH, (
        f"START_PTR={oracle['start_ptr']}: window does not wrap — adjust TRIG_COUNT"
    )
    assert axis_bytes == oracle["blob"], (
        "concurrent DATA_BASE reads corrupted the AXIS burst — the arbiter let a "
        "register read steal port B mid-cell (REA-REQ-916):\n"
        f"  axis  ={axis_bytes.hex()}\n  oracle={oracle['blob'].hex()}"
    )
    dut._log.info(
        f"REA-P2.5 REQ-916 PASS — {len(sink.beats)}-beat burst byte-identical to "
        "the post-dump DATA_BASE walk despite DATA_BASE hammered throughout the dump"
    )


if __name__ == "__main__":
    main()
