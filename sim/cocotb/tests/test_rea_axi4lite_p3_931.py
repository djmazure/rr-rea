# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_axi4lite — AXI4-Lite bridge onto the rr_rea register bus (RTL-P3.931).
#
# Covers REA-REQ-903..907. The DUT is a small VHDL harness that wires the
# bridge to a REAL rr_rea_top elaborated with G_REG_IFACE = "external", so
# these are not bridge-in-a-vacuum tests: a read has to survive the regbank's
# REGISTERED read path, which is the one thing a naive bridge gets wrong.
#
# BFM stance (feedback_bfm_no_dut_workarounds): the master here is hostile
# where the spec permits — it presents W before AW, backpressures both
# responses, and issues back-to-back writes with no idle cycle. Those are legal
# master behaviours that several real interconnects exhibit, and each one is a
# way a plausible bridge breaks.

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

_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

GENERICS = {
    "G_SAMPLE_W": 8, "G_DEPTH": 512, "G_TRIG_CONDS": 1,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1, "G_NUM_SOURCE": 1,
}

# Frozen register contract (same addresses the JTAG path uses — that is the
# point of REA-REQ-900: one register map, two bridges).
ADDR_VERSION  = 0x00
ADDR_CTRL     = 0x04
ADDR_STATUS   = 0x08
ADDR_SAMPLE_W = 0x0C
ADDR_DEPTH    = 0x10
ADDR_PRETRIG  = 0x14
ADDR_POSTTRIG = 0x18
ADDR_START_PTR = 0xC8
ADDR_DATA_BASE = 0x100

EXPECTED_VERSION = 0x52454109  # v0.9 feature tier (rr_rea_pkg C_REA_VERSION)

ACLK_PERIOD_NS = 10.0
SAMPLE_PERIOD_NS = 8.0


def main() -> None:
    run_simulation(
        top_level="rr_rea_axi_harness",
        module="test_rea_axi4lite_p3_931",
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
                f"{_FIX}/rr_rea_axi_harness.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── AXI4-Lite master BFM ─────────────────────────────────────────────


async def _start(dut):
    cocotb.start_soon(Clock(dut.aclk_i, ACLK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(
        Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start()
    )
    dut.aresetn_i.value = 0
    dut.awvalid_i.value = 0
    dut.wvalid_i.value = 0
    dut.arvalid_i.value = 0
    dut.bready_i.value = 1
    dut.rready_i.value = 1
    dut.wstrb_i.value = 0xF
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0
    dut.tck_i.value = 0
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    dut.arst_i.value = 1
    await ClockCycles(dut.aclk_i, 5)
    dut.aresetn_i.value = 1
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.aclk_i, 3)


async def _axi_write(dut, addr, data, *, w_first=False, both=False):
    """One AXI4-Lite write. Channel order is a parameter, because it is a
    parameter for a real master too (REA-REQ-905)."""
    if both:
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
    else:
        first_v, first_r = (
            (dut.wvalid_i, dut.wready_o) if w_first
            else (dut.awvalid_i, dut.awready_o)
        )
        second_v, second_r = (
            (dut.awvalid_i, dut.awready_o) if w_first
            else (dut.wvalid_i, dut.wready_o)
        )
        dut.awaddr_i.value = addr
        dut.wdata_i.value = data
        first_v.value = 1
        while True:
            await RisingEdge(dut.aclk_i)
            if first_r.value == 1:
                break
        first_v.value = 0
        await ClockCycles(dut.aclk_i, 2)  # a deliberate gap between halves
        second_v.value = 1
        while True:
            await RisingEdge(dut.aclk_i)
            if second_r.value == 1:
                break
        second_v.value = 0

    # Await the write response.
    while True:
        await RisingEdge(dut.aclk_i)
        if dut.bvalid_o.value == 1 and dut.bready_i.value == 1:
            break
    assert int(dut.bresp_o.value) == 0, "write response must be OKAY"


async def _axi_read(dut, addr):
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


# ── Tests ────────────────────────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-903")
async def test_rea_req_903_read_returns_the_addressed_register(dut):
    """The bridge reaches the real regbank through the external bus."""
    await _start(dut)
    got = await _axi_read(dut, ADDR_VERSION)
    assert got == EXPECTED_VERSION, (
        f"VERSION over AXI4-Lite = 0x{got:08X}, expected "
        f"0x{EXPECTED_VERSION:08X}. The bridge is not reaching the regbank, "
        "or is returning the wrong register."
    )
    dut._log.info("REA-REQ-903 PASS — VERSION 0x%08X over AXI", got)


@cocotb.test()
@requires("REA-REQ-904")
async def test_rea_req_904_consecutive_reads_do_not_lag_by_one(dut):
    """THE defect this bridge is shaped around.

    rr_rea_regbank's rd_data_o is REGISTERED (RTL-P1.96). A bridge that
    samples in the same cycle it presents the address returns the PREVIOUS
    register — and a single read still 'works', so only a SEQUENCE catches
    it. Read three registers with distinct constant values back to back and
    assert each is its own, not its predecessor's.
    """
    await _start(dut)
    expected = [
        (ADDR_VERSION, EXPECTED_VERSION),
        (ADDR_SAMPLE_W, GENERICS["G_SAMPLE_W"]),
        (ADDR_DEPTH, GENERICS["G_DEPTH"]),
    ]
    got = [await _axi_read(dut, addr) for addr, _ in expected]

    for (addr, want), have in zip(expected, got):
        assert have == want, (
            f"read 0x{addr:02X} = 0x{have:08X}, expected 0x{want:08X}. "
            "If each value is the PREVIOUS register's, the bridge is "
            "sampling rdata a cycle early (REA-REQ-904)."
        )
    # Explicit anti-lag statement: no value may equal its predecessor's.
    assert got[1] != got[0] and got[2] != got[1], (
        f"consecutive reads returned {[hex(v) for v in got]} — a lag-by-one "
        "signature"
    )
    dut._log.info("REA-REQ-904 PASS — no one-register lag")


@cocotb.test()
@requires("REA-REQ-903")
async def test_rea_req_903_write_then_readback(dut):
    await _start(dut)
    await _axi_write(dut, ADDR_PRETRIG, 0x2A)
    got = await _axi_read(dut, ADDR_PRETRIG)
    assert got == 0x2A, f"PRETRIG wrote 0x2A, read back 0x{got:08X}"

    await _axi_write(dut, ADDR_POSTTRIG, 0x15)
    assert await _axi_read(dut, ADDR_POSTTRIG) == 0x15
    # The earlier write must survive the later one (no address aliasing).
    assert await _axi_read(dut, ADDR_PRETRIG) == 0x2A
    dut._log.info("REA-REQ-903 PASS — write/readback, no aliasing")


@cocotb.test()
@requires("REA-REQ-905")
async def test_rea_req_905_channel_order_is_free(dut):
    """aw-first, w-first and simultaneous must all work identically."""
    await _start(dut)
    for order, kwargs in (
        ("aw-first", {}),
        ("w-first", {"w_first": True}),
        ("simultaneous", {"both": True}),
    ):
        value = 0x11 + len(order)
        await _axi_write(dut, ADDR_PRETRIG, value, **kwargs)
        got = await _axi_read(dut, ADDR_PRETRIG)
        assert got == value, (
            f"{order} write of 0x{value:02X} read back 0x{got:08X} — a master "
            "that presents W before AW is legal and several interconnects do "
            "it under backpressure (REA-REQ-905)"
        )
    dut._log.info("REA-REQ-905 PASS — all three channel orders")


@cocotb.test()
@requires("REA-REQ-906")
async def test_rea_req_906_responses_survive_backpressure(dut):
    """A master that holds bready/rready low must not lose its response."""
    await _start(dut)

    # Write with bready deasserted: bvalid must assert and HOLD.
    dut.bready_i.value = 0
    dut.awaddr_i.value = ADDR_PRETRIG
    dut.awvalid_i.value = 1
    dut.wdata_i.value = 0x33
    dut.wvalid_i.value = 1
    await ClockCycles(dut.aclk_i, 2)
    dut.awvalid_i.value = 0
    dut.wvalid_i.value = 0
    for _ in range(20):
        await RisingEdge(dut.aclk_i)
        if dut.bvalid_o.value == 1:
            break
    assert dut.bvalid_o.value == 1, "bvalid never asserted"
    for _ in range(10):
        await RisingEdge(dut.aclk_i)
        assert dut.bvalid_o.value == 1, (
            "bvalid dropped while bready was low — a dropped response "
            "deadlocks the master (REA-REQ-906)"
        )
    dut.bready_i.value = 1
    await RisingEdge(dut.aclk_i)
    await RisingEdge(dut.aclk_i)

    # Same for the read response.
    dut.rready_i.value = 0
    dut.araddr_i.value = ADDR_VERSION
    dut.arvalid_i.value = 1
    await ClockCycles(dut.aclk_i, 2)
    dut.arvalid_i.value = 0
    for _ in range(20):
        await RisingEdge(dut.aclk_i)
        if dut.rvalid_o.value == 1:
            break
    assert dut.rvalid_o.value == 1, "rvalid never asserted"
    held = int(dut.rdata_o.value)
    for _ in range(10):
        await RisingEdge(dut.aclk_i)
        assert dut.rvalid_o.value == 1, "rvalid dropped while rready was low"
        assert int(dut.rdata_o.value) == held, (
            "rdata changed while the response was outstanding"
        )
    assert held == EXPECTED_VERSION
    dut.rready_i.value = 1
    dut._log.info("REA-REQ-906 PASS — responses held under backpressure")


@cocotb.test()
@requires("REA-REQ-907")
async def test_rea_req_907_back_to_back_writes_do_not_merge_or_double_pulse(dut):
    """One AXI transaction, exactly one reg-bus write.

    A double pulse on a TOGGLE register is a silent no-op — CTRL's arm_toggle
    would arm and immediately re-arm, and the capture never starts, with every
    register looking correct afterwards.
    """
    await _start(dut)

    pulses = 0

    async def _count():
        nonlocal pulses
        while True:
            await RisingEdge(dut.aclk_i)
            if dut.reg_wr_en_probe_o.value == 1:
                pulses += 1

    counter = cocotb.start_soon(_count())

    await _axi_write(dut, ADDR_PRETRIG, 0x01)
    await _axi_write(dut, ADDR_PRETRIG, 0x02)
    await _axi_write(dut, ADDR_POSTTRIG, 0x03)
    await ClockCycles(dut.aclk_i, 5)
    counter.kill()

    assert pulses == 3, (
        f"3 AXI writes produced {pulses} register-bus write strobes. "
        "More means a stretched or double-pulsed strobe (fatal on a toggle "
        "register); fewer means writes merged (REA-REQ-907)."
    )
    assert await _axi_read(dut, ADDR_PRETRIG) == 0x02
    assert await _axi_read(dut, ADDR_POSTTRIG) == 0x03
    dut._log.info("REA-REQ-907 PASS — 3 writes, 3 strobes")


@cocotb.test()
@requires("REA-REQ-901")
async def test_rea_req_901_tap_is_inert_in_external_mode(dut):
    """Two live masters on one register bus is the corruption this forbids.

    With G_REG_IFACE = "external" the TAP decoder is not instantiated at all,
    so a full capture/shift/update sequence must move nothing.
    """
    await _start(dut)
    await _axi_write(dut, ADDR_PRETRIG, 0x5A)
    before = await _axi_read(dut, ADDR_PRETRIG)
    assert before == 0x5A

    # Drive a complete, well-formed TAP sequence — the exact wiggling that
    # would write a register on the JTAG path.
    dut.sel_i.value = 1
    for _ in range(4):
        dut.capture_i.value = 1
        await ClockCycles(dut.aclk_i, 2)
        dut.capture_i.value = 0
        dut.shift_en_i.value = 1
        for bit in range(49):
            dut.tdi_i.value = bit & 1
            dut.tck_i.value = 1
            await ClockCycles(dut.aclk_i, 1)
            dut.tck_i.value = 0
            await ClockCycles(dut.aclk_i, 1)
        dut.shift_en_i.value = 0
        dut.update_i.value = 1
        await ClockCycles(dut.aclk_i, 2)
        dut.update_i.value = 0
    dut.sel_i.value = 0
    await ClockCycles(dut.aclk_i, 5)

    after = await _axi_read(dut, ADDR_PRETRIG)
    assert after == before, (
        f"PRETRIG moved 0x{before:02X} -> 0x{after:02X} after TAP wiggling. "
        "In external mode the TAP must be inert (REA-REQ-901)."
    )
    assert dut.tdo_o.value == 0, "tdo_o must be held low in external mode"
    dut._log.info("REA-REQ-901 PASS — TAP inert, tdo low")


@cocotb.test()
@requires("REA-REQ-908", "REA-REQ-904")
async def test_rea_req_908_lean_profile_captures(dut):
    """The lean profile is a CONFIGURATION — it must still capture.

    G_SAMPLE_W=8, G_DEPTH=512, G_TRIG_CONDS=1, no timestamp plane. Arm, feed a
    counter on probe_i, and confirm the core reaches done through the AXI
    control path alone.
    """
    await _start(dut)
    await _axi_write(dut, ADDR_PRETRIG, 4)
    await _axi_write(dut, ADDR_POSTTRIG, 8)

    async def _drive_probe():
        value = 0
        while True:
            await RisingEdge(dut.sample_clk_i)
            dut.probe_i.value = value & 0xFF
            value += 1

    driver = cocotb.start_soon(_drive_probe())

    ctrl = await _axi_read(dut, ADDR_CTRL)
    await _axi_write(dut, ADDR_CTRL, ctrl ^ 0x01)  # arm toggle

    done = False
    for _ in range(400):
        status = await _axi_read(dut, ADDR_STATUS)
        if status & 0x04:
            done = True
            break
        await ClockCycles(dut.aclk_i, 20)
    driver.kill()

    assert done, (
        "the lean profile never reached done via the AXI control path — "
        "STATUS.done stayed low (REA-REQ-908)"
    )
    dut._log.info("REA-REQ-908 PASS — lean profile captured to done")

    # REA-REQ-904 on the DATA window (REA-P2.4). The regbank registers above
    # are ONE edge behind the address; a capture cell is TWO (BRAM read, then
    # the registered paging mux). The 1.1.0 bridge waited one and returned the
    # cell addressed BEFORE each read — every DATA_BASE address read as the
    # same stale cell, so a bridge suite that never read the window was green
    # on a door that could not dump a capture. Consecutive cells of a counter
    # capture must be distinct and step by exactly one.
    start_ptr = await _axi_read(dut, ADDR_START_PTR) % GENERICS["G_DEPTH"]
    cells = []
    for i in range(6):
        phys = (start_ptr + i) % GENERICS["G_DEPTH"]
        cells.append(await _axi_read(dut, ADDR_DATA_BASE + 4 * phys) & 0xFF)
    for i in range(1, len(cells)):
        assert (cells[i] - cells[i - 1]) & 0xFF == 1, (
            f"DATA_BASE cells from START_PTR={start_ptr} read {cells} over AXI — "
            "consecutive capture cells must step by one; identical/stale cells "
            "are the DATA-window read-latency lag (REA-REQ-904)"
        )
    dut._log.info(f"REA-REQ-904 PASS — DATA window cells {cells} distinct over AXI")


if __name__ == "__main__":
    main()
