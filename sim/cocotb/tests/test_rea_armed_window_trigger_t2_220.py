# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# RTL-T2.220 — the DISCRIMINATING experiment for "the trigger does not fire
# when its condition becomes true DURING an armed capture".
#
# The bench symptom (Zybo Z7-20, bit 0xb497aac6): the rea_source_demo's
# designed pre-trigger scenario — arm while run_enable is HELD at 0, then
# release it over JTAG so the pre-trigger window shows the counter constant —
# times out with "core never signalled capture-complete", while the SAME
# trigger fires correctly when run_enable is already 1 BEFORE arming. The
# SOURCE write itself reported before=0x0 after=0x1 and the value persisted.
#
# Two candidate mechanisms were open, and they demand different fixes:
#   (a) the trigger condition is only evaluated at/near arm, so a condition
#       that becomes true later is never seen — an RTL defect;
#   (b) the concurrent second xsdb/hw_server session needed to drive SOURCE
#       while the capture process holds the cable is the confound — a
#       host/JTAG-arbitration defect, and (if true) a serious usability one,
#       since a JTAG-only design has no other way to change the condition.
#
# This test separates them. It reproduces the demo end-to-end THROUGH THE JTAG
# REGISTER INTERFACE — comparator-array trigger on the run_enable bit (the path
# `conditions:` in a debug yml actually takes, not the legacy comparator), arm
# while the bit is 0, release it MID-ARMED-WINDOW via a real SOURCE register
# write, poll STATUS for done — with ONE physical TAP and ONE session. So:
#
#   * fails here  ⇒ (a): the core cannot see a late condition. RTL fix.
#   * passes here ⇒ (a) is REFUTED and the cause is above the core — the
#     second JTAG session or the host, which is where to look next.
#
# The demo's probe word is modelled faithfully, including the gate: the counter
# only advances while source_o(0) is high, so the pre-trigger half really is a
# held counter, and `free_tick` toggles regardless (the liveness channel).
#
# Contract note: no REQ asserted this. REA-REQ-100..107 pin the sliding window
# and the pointer arithmetic — the OUTPUT mapping — but nothing said the
# comparator is evaluated on EVERY armed cycle rather than at arm. That silence
# is why a shipped demo could document a scenario the hardware had never been
# shown to perform. Added as REA-REQ-108.

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

DEPTH = 256
GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": DEPTH,
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": 4, "G_NUM_SOURCE": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parents[3] / "rtl")
_FIX = str(_Path(__file__).resolve().parent / "fixtures")

# Register map — the frozen host contract (sdk/cli/rea/registers.py).
ADDR_CTRL      = 0x04
ADDR_STATUS    = 0x08
ADDR_PRETRIG   = 0x14
ADDR_POSTTRIG  = 0x18
ADDR_TRIG_MODE = 0x20
ADDR_COND_SEL  = 0x30
ADDR_COND_CFG  = 0x34
ADDR_COND_VAL  = 0x38
ADDR_SOURCE    = 0x3C

CTRL_BIT_ARM        = 0x01
STATUS_BIT_TRIGGERED = 0x02
STATUS_BIT_DONE      = 0x04

TRIG_MODE_VALUE_MATCH = 0x01
TRIG_MODE_BIT_ARRAY   = 0x04
TRIG_OP_EQ = 0

# Demo probe word (debug/u_dbg_rea.yml): counter[7:0], run_enable_q[8],
# direction_q[9], load_q[10], free_tick[11].
BIT_RUN_ENABLE = 8
BIT_FREE_TICK  = 11

PRETRIG  = DEPTH // 2 - 1
POSTTRIG = DEPTH // 2 - 1


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_armed_window_trigger_t2_220",
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
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── JTAG protocol helpers (same wire format as test_rea_top.py) ──────

SAMPLE_PERIOD_NS = 8.0     # 125 MHz sample clock — the demo's
TCK_PERIOD_NS    = 25.0    # 40 MHz JTAG clock


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk_i, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst_i.value = 1
    dut.arst_i.value = 1
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    dut.probe_i.value = 0
    await ClockCycles(dut.tck_i, 4)
    dut.sample_rst_i.value = 0
    dut.arst_i.value = 0
    await ClockCycles(dut.tck_i, 1)


async def _capture_phase(dut):
    dut.sel_i.value = 1
    dut.capture_i.value = 1
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    await RisingEdge(dut.tck_i)
    dut.capture_i.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    from cocotb.triggers import NextTimeStep
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


async def _jtag_write(dut, addr: int, data: int):
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


def _cond_cfg_word(op: int, width: int, lsb: int, valid: bool = True) -> int:
    """{valid[31], lsb_hi[30:28], op[27:24], width[23:16], lsb_lo[15:8]}."""
    return (((1 if valid else 0) & 1) << 31) | (((lsb >> 8) & 0x7) << 28) \
        | ((op & 0xF) << 24) | ((width & 0xFF) << 16) | ((lsb & 0xFF) << 8)


# ── The demo's gated counter, driven off the core's own source output ──


async def _drive_demo_probe(dut, cycles: int):
    """Model rr_rea_demo_core: the counter advances only while source_o(0) is
    high; free_tick toggles regardless (the liveness channel that distinguishes
    "the gate works" from "the analyzer is dead")."""
    cnt = 0
    tick = 0
    for _ in range(cycles):
        run_enable = int(dut.source_o.value) & 1
        tick ^= 1
        dut.probe_i.value = ((cnt & 0xFF)
                             | (run_enable << BIT_RUN_ENABLE)
                             | (tick << BIT_FREE_TICK))
        await RisingEdge(dut.sample_clk_i)
        if run_enable:
            cnt = (cnt + 1) & 0xFF


async def _configure_run_enable_trigger(dut, pretrig: int = PRETRIG,
                                        posttrig: int = POSTTRIG):
    """Exactly what the host does for `conditions: [{run_enable_q, 1}]`."""
    await _jtag_write(dut, ADDR_PRETRIG, pretrig)
    await _jtag_write(dut, ADDR_POSTTRIG, posttrig)
    await _jtag_write(dut, ADDR_TRIG_MODE,
                      TRIG_MODE_VALUE_MATCH | TRIG_MODE_BIT_ARRAY)
    await _jtag_write(dut, ADDR_COND_SEL, 0)
    await _jtag_write(dut, ADDR_COND_CFG,
                      _cond_cfg_word(TRIG_OP_EQ, width=1, lsb=BIT_RUN_ENABLE))
    await _jtag_write(dut, ADDR_COND_VAL, 1)
    await _jtag_write(dut, ADDR_COND_SEL, 0)


async def _poll_done(dut, polls: int) -> tuple[bool, int]:
    status = 0
    for _ in range(polls):
        status = await _jtag_read(dut, ADDR_STATUS)
        if status & STATUS_BIT_DONE:
            return True, status
        await ClockCycles(dut.tck_i, 4)
    return False, status


# ── The experiment ───────────────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-108")
async def test_trigger_fires_when_condition_becomes_true_while_armed(dut):
    """THE discriminating case (RTL-T2.220). Arm with run_enable held at 0,
    release it mid-armed-window with a real SOURCE write, expect done."""
    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_demo_probe(dut, 200_000))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)   # warm the sliding window

    await _configure_run_enable_trigger(dut)

    # Arm while the condition is FALSE — the whole point of a pretrigger window.
    assert (int(dut.probe_i.value) >> BIT_RUN_ENABLE) & 1 == 0, (
        "precondition broken: run_enable was already high at arm, which is the "
        "case that ALREADY works on silicon")
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    # Sit armed with the condition false. It must NOT fire (that is the
    # pre-trigger window), and the host polls STATUS throughout, exactly as the
    # capture process does.
    fired_early, status = await _poll_done(dut, 20)
    assert not fired_early, (
        f"fired with run_enable still 0 — the comparator is not looking at the "
        f"condition at all (STATUS=0x{status:02X})")

    # Release the gate over JTAG — the SOURCE write the demo documents.
    await _jtag_write(dut, ADDR_SOURCE, 1)
    readback = await _jtag_read(dut, ADDR_SOURCE)
    assert readback & 1 == 1, f"SOURCE write did not stick: 0x{readback:08X}"

    done, status = await _poll_done(dut, 400)
    assert done, (
        "RTL-T2.220: the trigger did NOT fire although its condition became "
        f"true during the armed window (last STATUS=0x{status:02X}, "
        f"triggered={bool(status & STATUS_BIT_TRIGGERED)}). One TAP, one "
        "session — so the concurrent-JTAG-session hypothesis is not available "
        "as an explanation here: the core itself only evaluates the trigger "
        "at/near arm.")


@cocotb.test()
@requires("REA-REQ-108")
async def test_trigger_fires_with_no_jtag_traffic_during_the_wait(dut):
    """Isolates the polling itself. Same release, but the host is SILENT from
    arm until well after the condition goes true — if the capture only completes
    when something is shifting on the TAP, the fire is being driven by JTAG
    activity rather than by the sample-clock comparator."""
    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_demo_probe(dut, 200_000))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    await _configure_run_enable_trigger(dut)
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    await ClockCycles(dut.sample_clk_i, 4 * DEPTH)    # armed, silent, condition false
    await _jtag_write(dut, ADDR_SOURCE, 1)
    await ClockCycles(dut.sample_clk_i, 4 * DEPTH)    # silent again while it completes

    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_BIT_DONE, (
        f"capture did not complete without JTAG polling (STATUS=0x{status:02X})")


@cocotb.test()
@requires("REA-REQ-108")
async def test_a_late_condition_does_not_fire_before_it_is_true(dut):
    """ADVERSARIAL companion. A test that only checks "it eventually fires"
    passes on a core that free-fires on arm and captures garbage — which would
    look identical to the operator. The capture must stay untriggered for the
    whole interval the condition is false."""
    await _start_clocks(dut)
    await _reset(dut)
    cocotb.start_soon(_drive_demo_probe(dut, 200_000))
    await ClockCycles(dut.sample_clk_i, 2 * DEPTH)

    await _configure_run_enable_trigger(dut)
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    for _ in range(8):
        await ClockCycles(dut.sample_clk_i, DEPTH)
        status = await _jtag_read(dut, ADDR_STATUS)
        assert not (status & (STATUS_BIT_TRIGGERED | STATUS_BIT_DONE)), (
            f"triggered while run_enable was still 0 (STATUS=0x{status:02X}) — "
            "the pre-trigger window would hold arbitrary traffic, not the held "
            "counter the demo promises")


if __name__ == "__main__":
    main()
