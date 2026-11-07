# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.32 — boot-time reset-armed capture at rr_rea_top (REA-REQ-977, 978).

With G_ARM_AT_RESET = True, the core leaves sample_rst_i ARMED with the
synthesis-time G_RESET_* configuration in place, while TCK is completely
stopped (no host attached, zero JTAG register writes). When the boot-time
transition occurs on probe_i, the core triggers, collects G_RESET_POSTTRIG
samples, freezes the window, and runs its readback-integrity CRC sweep.

When the host later attaches (starts TCK) and performs ONLY register reads
(zero register writes), it reads:
- VERSION (0x00) = 0x52454113 (v0.19)
- STATUS (0x08): done=1, triggered=1, armed=0, crc_valid=1, armed_at_reset=1 (bit 10)
- PRETRIG (0x14), POSTTRIG (0x18), CAPTURE_LEN (0x1C), TRIG_MODE (0x20),
  TRIG_VALUE (0x24), TRIG_MASK (0x28), PRETRIG_VALID (0xF0), START_PTR (0xC8)
- CAPTURE_EPOCH (0xEC) = 1: the reset arm is the first accepted arm
- The frozen DATA_BASE window holding the exact boot transition.

While STATUS[10] (armed_at_reset) is 1 (either while armed waiting for the boot
event or after the boot capture has completed), a selftest fill (SELFTEST_CTRL)
is refused (STATUS[7] = 1) so an attaching host's selftest path cannot overwrite
the boot capture before it is read or explicitly reset. A soft reset (CTRL[1])
or host arm (CTRL[0]) clears STATUS[10] and allows subsequent selftest or
host-configured captures.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

_tb = str(Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
_sim = str(Path(__file__).resolve().parents[2])
if _sim not in _sys.path:
    _sys.path.insert(0, _sim)
del _tb, _sim

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402
from test_rea_storage_qual_top_p2_7 import _jtag_read, _jtag_write  # noqa: E402

SAMPLE_W = 12
DEPTH = 64
RESET_PRETRIG = 8
RESET_POSTTRIG = 10
RESET_CAPTURE_LEN = 19
RESET_TRIG_MODE = 0x01
RESET_TRIG_VALUE = 0x124
RESET_TRIG_MASK = 0xFFF

GENERICS = {
    "G_SAMPLE_W": SAMPLE_W,
    "G_DEPTH": DEPTH,
    "G_TIMESTAMP_W": 32,
    "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": 4,
    "G_QUAL_CONDS": 0,
    "G_ARM_AT_RESET": True,
    "G_RESET_PRETRIG": RESET_PRETRIG,
    "G_RESET_POSTTRIG": RESET_POSTTRIG,
    "G_RESET_TRIG_MODE": RESET_TRIG_MODE,
    "G_RESET_TRIG_VALUE": RESET_TRIG_VALUE,
    "G_RESET_TRIG_MASK": RESET_TRIG_MASK,
    "G_RESET_DECIM": 0,
}

_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_VERSION = 0x00
ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_CAPTURE_LEN = 0x1C
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_START_PTR = 0xC8
ADDR_SELFTEST_CTRL = 0xDC
ADDR_CRC_SAMPLE = 0xE4
ADDR_CRC_TS = 0xE8
ADDR_CAPTURE_EPOCH = 0xEC
ADDR_PRETRIG_VALID = 0xF0
ADDR_DATA_BASE = 0x100

CTRL_ARM = 1 << 0
CTRL_RESET = 1 << 1

STATUS_ARMED = 1 << 0
STATUS_TRIGGERED = 1 << 1
STATUS_DONE = 1 << 2
STATUS_OVERFLOW = 1 << 3
STATUS_CRC_VALID = 1 << 4
STATUS_SELFTEST_MODE = 1 << 6
STATUS_SELFTEST_REFUSED = 1 << 7
STATUS_FORCED = 1 << 9
STATUS_ARMED_AT_RESET = 1 << 10

VERSION_V0_19 = 0x52454113

# Hard-coded expected 19-sample boot window around RESET_TRIG_VALUE = 0x124:
# 8 pre-trigger samples (0x11C..0x123), trigger cell 0x124 at index 8, and
# 10 post-trigger samples (0x125..0x12E).
EXPECTED_BOOT_WINDOW = [
    0x11C, 0x11D, 0x11E, 0x11F, 0x120, 0x121, 0x122, 0x123,
    0x124,
    0x125, 0x126, 0x127, 0x128, 0x129, 0x12A, 0x12B, 0x12C, 0x12D, 0x12E,
]

# Hard-coded expected 9-sample host re-arm window around 0x220 (PRETRIG=4, POSTTRIG=4):
EXPECTED_HOST_WINDOW = [
    0x21C, 0x21D, 0x21E, 0x21F,
    0x220,
    0x221, 0x222, 0x223, 0x224,
]


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


def _init_idle_tap(dut) -> None:
    dut.arst_i.value = 0
    dut.tck_i.value = 0
    dut.tdi_i.value = 0
    dut.capture_i.value = 0
    dut.shift_en_i.value = 0
    dut.update_i.value = 0
    dut.sel_i.value = 0
    dut.ext_trigger_i.value = 0
    dut.trigger_i.value = 0


async def _read_window(dut, count: int) -> list[int]:
    start = await _jtag_read(dut, ADDR_START_PTR) & (DEPTH - 1)
    cells = []
    for idx in range(count):
        addr = ADDR_DATA_BASE + 4 * ((start + idx) % DEPTH)
        cells.append(await _jtag_read(dut, addr) & 0xFFF)
    return cells


async def _boot_capture_and_attach_tck(dut) -> int:
    _init_idle_tap(dut)
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0x100
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())

    # Hold sample_rst_i while TCK is completely stopped (no host attached).
    await ClockCycles(dut.sample_clk_i, 6)
    dut.sample_rst_i.value = 0

    # Wait for the internal 2-stage reset synchronizer to release, then drive
    # the boot sequence 0x100..0x13F on probe_i while TCK stays 0.
    trigger_pulses = 0
    for step in range(64):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x100 + step
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.trigger_pulse_o.value) == 1:
            trigger_pulses += 1

    # Hold filler while the post-capture CRC sweep and publication settle run,
    # still with TCK stopped.
    await FallingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0xAAA
    for _ in range(160):
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.trigger_pulse_o.value) == 1:
            trigger_pulses += 1

    # Exit ReadOnly before starting the TCK clock generator.
    await FallingEdge(dut.sample_clk_i)
    cocotb.start_soon(Clock(dut.tck_i, 25.0, unit="ns").start())
    await ClockCycles(dut.tck_i, 6)
    return trigger_pulses


async def _reset_waiting_tck_running(dut) -> None:
    """sample_rst_i with TCK running and probe_i held away from the reset
    trigger value: the core is left waiting for its boot event."""
    _init_idle_tap(dut)
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0x055
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, 25.0, unit="ns").start())
    await ClockCycles(dut.sample_clk_i, 6)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 12)
    await ClockCycles(dut.tck_i, 6)


async def _drive_boot_event(dut) -> None:
    """Present the boot sequence 0x100..0x13F (it holds RESET_TRIG_VALUE),
    then filler, long enough for a capture, its CRC sweep and publication."""
    for step in range(64):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x100 + step
    await FallingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0xAAA
    await ClockCycles(dut.sample_clk_i, 160)
    await ClockCycles(dut.tck_i, 6)


async def _settle(dut) -> None:
    await ClockCycles(dut.sample_clk_i, 16)
    await ClockCycles(dut.tck_i, 6)


@cocotb.test()
@requires("REA-REQ-977", "REA-REQ-978", "REA-REQ-019", "REA-REQ-806")
async def test_rea_req_977_978_boot_capture_with_zero_host_writes(dut):
    """reset -> boot event -> trigger -> window holds the event with NO host
    write and before TCK ever toggles."""
    trigger_pulses = await _boot_capture_and_attach_tck(dut)
    assert trigger_pulses == 1, (
        f"REA-REQ-977: expected exactly 1 boot trigger_pulse_o before host "
        f"attach, saw {trigger_pulses}"
    )

    version = await _jtag_read(dut, ADDR_VERSION)
    assert version == VERSION_V0_19, (
        f"REA-REQ-019/978: VERSION=0x{version:08X}, want 0x{VERSION_V0_19:08X}"
    )
    assert (version & 1) == 1, "REA-REQ-806: VERSION tier byte must stay odd"

    status = await _jtag_read(dut, ADDR_STATUS)
    expected_status = STATUS_TRIGGERED | STATUS_DONE | STATUS_CRC_VALID | STATUS_ARMED_AT_RESET
    assert status == expected_status, (
        f"REA-REQ-977/978: STATUS=0x{status:08X}, want 0x{expected_status:08X} "
        "(triggered|done|crc_valid|armed_at_reset, not armed/overflow/forced)"
    )

    assert await _jtag_read(dut, ADDR_PRETRIG) == RESET_PRETRIG
    assert await _jtag_read(dut, ADDR_POSTTRIG) == RESET_POSTTRIG
    assert await _jtag_read(dut, ADDR_CAPTURE_LEN) == RESET_CAPTURE_LEN
    assert await _jtag_read(dut, ADDR_TRIG_MODE) == RESET_TRIG_MODE
    assert await _jtag_read(dut, ADDR_TRIG_VALUE) == RESET_TRIG_VALUE
    assert await _jtag_read(dut, ADDR_TRIG_MASK) == RESET_TRIG_MASK
    assert await _jtag_read(dut, ADDR_PRETRIG_VALID) == RESET_PRETRIG
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 1, (
        f"REA-REQ-977: boot capture CAPTURE_EPOCH={epoch}, want 1 (the reset "
        "arm is the first accepted arm, SPEC 'Boot-time capture')"
    )
    assert await _jtag_read(dut, ADDR_CRC_SAMPLE) != 0
    assert await _jtag_read(dut, ADDR_CRC_TS) != 0

    window = await _read_window(dut, RESET_CAPTURE_LEN)
    assert window == EXPECTED_BOOT_WINDOW, (
        f"REA-REQ-977: frozen boot window {window} != {EXPECTED_BOOT_WINDOW}"
    )


@cocotb.test()
@requires("REA-REQ-978")
async def test_rea_req_978_selftest_refused_and_cleared_by_reset_or_rearm(dut):
    """While armed_at_reset is active (both after the boot capture finishes and
    while armed waiting for the boot event), SELFTEST_CTRL is refused and does
    not overwrite the boot window. CTRL[1] reset and CTRL[0] arm clear
    STATUS[10] and allow normal host operation."""
    await _boot_capture_and_attach_tck(dut)
    # 1. On the completed boot capture, attempt a selftest fill.
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 1)
    await ClockCycles(dut.sample_clk_i, 16)
    await ClockCycles(dut.tck_i, 6)

    status = await _jtag_read(dut, ADDR_STATUS)
    assert (status & STATUS_SELFTEST_REFUSED) != 0, (
        f"REA-REQ-978: selftest fill was not refused on completed boot capture "
        f"(STATUS=0x{status:08X})"
    )
    assert (status & STATUS_SELFTEST_MODE) == 0
    assert (status & STATUS_ARMED_AT_RESET) != 0
    assert await _read_window(dut, RESET_CAPTURE_LEN) == EXPECTED_BOOT_WINDOW

    # 2. Hard sample_rst_i with probe_i held away from the trigger value:
    # the core leaves reset ARMED with STATUS[10]=1 waiting for the boot event,
    # and a selftest fill while waiting is also refused.
    dut.probe_i.value = 0x055
    dut.sample_rst_i.value = 1
    await ClockCycles(dut.sample_clk_i, 6)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 12)
    await ClockCycles(dut.tck_i, 6)

    status = await _jtag_read(dut, ADDR_STATUS)
    assert (status & (STATUS_ARMED | STATUS_ARMED_AT_RESET)) == (
        STATUS_ARMED | STATUS_ARMED_AT_RESET
    ), f"REA-REQ-978: after sample_rst_i STATUS=0x{status:08X}, want armed|armed_at_reset"
    assert (status & (STATUS_TRIGGERED | STATUS_DONE)) == 0

    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 0)
    await ClockCycles(dut.sample_clk_i, 16)
    await ClockCycles(dut.tck_i, 6)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert (status & STATUS_SELFTEST_REFUSED) != 0

    # 3. Soft reset (CTRL[1]) clears armed, armed_at_reset, and selftest_refused.
    await _jtag_write(dut, ADDR_CTRL, CTRL_RESET)
    await ClockCycles(dut.sample_clk_i, 16)
    await ClockCycles(dut.tck_i, 6)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert (
        status
        & (
            STATUS_ARMED
            | STATUS_TRIGGERED
            | STATUS_DONE
            | STATUS_SELFTEST_REFUSED
            | STATUS_ARMED_AT_RESET
        )
    ) == 0, f"REA-REQ-978: CTRL[1] did not clear armed_at_reset (STATUS=0x{status:08X})"

    # 4. Reprogram and arm from the host (CTRL[0]): captures the host pattern
    # with STATUS[10] (armed_at_reset) = 0.
    await _jtag_write(dut, ADDR_PRETRIG, 4)
    await _jtag_write(dut, ADDR_POSTTRIG, 4)
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0x220)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0xFFF)
    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    await ClockCycles(dut.sample_clk_i, 16)

    for step in range(48):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x200 + step
    await ClockCycles(dut.sample_clk_i, 160)
    await ClockCycles(dut.tck_i, 6)

    status = await _jtag_read(dut, ADDR_STATUS)
    assert (status & (STATUS_TRIGGERED | STATUS_DONE)) == (
        STATUS_TRIGGERED | STATUS_DONE
    )
    assert (status & STATUS_ARMED_AT_RESET) == 0, (
        f"REA-REQ-978: host-armed capture set STATUS[10] (STATUS=0x{status:08X})"
    )
    assert await _read_window(dut, 9) == EXPECTED_HOST_WINDOW



@cocotb.test()
@requires("REA-REQ-978")
async def test_rea_req_978_status_armed_at_reset_set_out_of_reset(dut):
    """STATUS[10] reads 1 out of sample_rst_i on a G_ARM_AT_RESET build, and
    nothing but a host CTRL write clears it: it is still 1 after the boot
    event has been presented. Reads STATUS[10] only, so it checks the flag
    and its register path, not whether the FSM captured."""
    await _reset_waiting_tck_running(dut)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_ARMED_AT_RESET, (
        f"REA-REQ-978: STATUS[10] (armed_at_reset) reads 0 out of sample_rst_i "
        f"with G_ARM_AT_RESET=true (STATUS=0x{status:08X})"
    )

    await _drive_boot_event(dut)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_ARMED_AT_RESET, (
        f"REA-REQ-978: STATUS[10] (armed_at_reset) cleared with no host CTRL "
        f"write (STATUS=0x{status:08X})"
    )


@cocotb.test()
@requires("REA-REQ-977", "REA-REQ-807")
async def test_rea_req_977_boot_arm_is_capture_epoch_1(dut):
    """CAPTURE_EPOCH reads 1 out of sample_rst_i on a G_ARM_AT_RESET build:
    the reset arm is the build's first accepted arm, so the boot capture is
    generation 1 (SPEC 'Boot-time capture'). The boot capture completing
    does not move it; the first host arm moves it to 2 (REA-REQ-807)."""
    await _reset_waiting_tck_running(dut)
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 1, (
        f"REA-REQ-977: CAPTURE_EPOCH reads {epoch} out of sample_rst_i, want 1 "
        "(the reset arm is the first accepted arm)"
    )

    await _drive_boot_event(dut)
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 1, (
        f"REA-REQ-807: CAPTURE_EPOCH reads {epoch} after the boot event, want 1 "
        "(a completed capture does not move the epoch)"
    )

    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    await _settle(dut)
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 2, (
        f"REA-REQ-807: CAPTURE_EPOCH reads {epoch} after the first host arm, "
        "want 2"
    )


@cocotb.test()
@requires("REA-REQ-978")
async def test_rea_req_978_selftest_released_only_by_host_clear(dut):
    """A selftest fill is refused while STATUS[10] is set, and a host CTRL[1]
    clears STATUS[10] and releases the selftest path: the next fill is
    accepted. The core is left waiting for its boot event, so this checks
    the flag's refusal and its clear, not the boot capture."""
    await _reset_waiting_tck_running(dut)
    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 1)
    await _settle(dut)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status & STATUS_SELFTEST_REFUSED, (
        f"REA-REQ-978: selftest fill accepted while armed at reset "
        f"(STATUS=0x{status:08X})"
    )
    assert not status & STATUS_SELFTEST_MODE, (
        f"REA-REQ-978: selftest mode entered while armed at reset "
        f"(STATUS=0x{status:08X})"
    )

    await _jtag_write(dut, ADDR_CTRL, CTRL_RESET)
    await _settle(dut)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert not status & STATUS_ARMED_AT_RESET, (
        f"REA-REQ-978: CTRL[1] left STATUS[10] (armed_at_reset) set "
        f"(STATUS=0x{status:08X})"
    )

    await _jtag_write(dut, ADDR_SELFTEST_CTRL, 0)
    await ClockCycles(dut.sample_clk_i, 4 * DEPTH)
    await _settle(dut)
    status = await _jtag_read(dut, ADDR_STATUS)
    assert not status & STATUS_SELFTEST_REFUSED, (
        f"REA-REQ-978: selftest fill still refused after CTRL[1] cleared "
        f"STATUS[10] (STATUS=0x{status:08X})"
    )
    assert status & STATUS_SELFTEST_MODE, (
        f"REA-REQ-978: selftest fill not accepted after CTRL[1] cleared "
        f"STATUS[10] (STATUS=0x{status:08X})"
    )


if __name__ == "__main__":
    main()
