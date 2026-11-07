# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.32 — G_ARM_AT_RESET = false ignores every G_RESET_* (REA-REQ-977).

rr_rea_top is elaborated with G_ARM_AT_RESET = false and EVERY G_RESET_*
generic set to a non-zero value. REA-REQ-977 says those values are ignored
and the core leaves reset disarmed and zeroed, as before P2.32. The test
checks that at the register surface a host reaches:

- out of reset: STATUS = 0 (not armed, STATUS[10] clear), CAPTURE_EPOCH = 0,
  and every configuration register a G_RESET_* would load reads its pre-P2.32
  reset value (0; CAPTURE_LEN = 1);
- the reset trigger pattern on probe_i does NOT fire the core;
- a host-armed capture then behaves as it did before P2.32: one
  trigger_pulse_o, the exact host window, CAPTURE_EPOCH = 1.

Every expected value is hard-coded (ROUTERTL-002). The same test is green on
the pre-P2.32 core (54e097c), which simply has no G_RESET_* generics.
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

GENERICS = {
    "G_SAMPLE_W": SAMPLE_W,
    "G_DEPTH": DEPTH,
    "G_TIMESTAMP_W": 32,
    "G_NUM_CHAN": 1,
    "G_TRIG_CONDS": 2,
    "G_QUAL_CONDS": 1,
    "G_ARM_AT_RESET": False,
    # Every G_RESET_* non-zero: with G_ARM_AT_RESET = false all are ignored.
    "G_RESET_PRETRIG": 9,
    "G_RESET_POSTTRIG": 11,
    "G_RESET_TRIG_MODE": 0x01,
    "G_RESET_TRIG_VALUE": 0x124,
    "G_RESET_TRIG_MASK": 0xFFF,
    "G_RESET_DECIM": 3,
    "G_RESET_COND_CFG": (0 << 24) | (8 << 16) | (0 << 8),
    "G_RESET_COND_VAL": 0x24,
    "G_RESET_QUAL_MODE": 1,
    "G_RESET_QUAL_CFG": (0 << 24) | (4 << 16) | (8 << 8),
    "G_RESET_QUAL_VAL": 0x1,
}

_RTL_DIR = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"

ADDR_CTRL = 0x04
ADDR_STATUS = 0x08
ADDR_PRETRIG = 0x14
ADDR_POSTTRIG = 0x18
ADDR_CAPTURE_LEN = 0x1C
ADDR_TRIG_MODE = 0x20
ADDR_TRIG_VALUE = 0x24
ADDR_TRIG_MASK = 0x28
ADDR_COND_CFG = 0x34
ADDR_COND_VAL = 0x38
ADDR_DECIM = 0xB0
ADDR_QUAL_MODE = 0xB4
ADDR_QUAL_CFG = 0xBC
ADDR_QUAL_VAL = 0xC0
ADDR_START_PTR = 0xC8
ADDR_CAPTURE_EPOCH = 0xEC
ADDR_DATA_BASE = 0x100

CTRL_ARM = 1 << 0

STATUS_TRIGGERED = 1 << 1
STATUS_DONE = 1 << 2
STATUS_CRC_VALID = 1 << 4

# Pre-P2.32 reset value of every register a G_RESET_* generic would load, in
# read order (CAPTURE_LEN = PRETRIG + POSTTRIG + 1 = 1).
RESET_REGISTERS = (
    ("PRETRIG", ADDR_PRETRIG, 0),
    ("POSTTRIG", ADDR_POSTTRIG, 0),
    ("CAPTURE_LEN", ADDR_CAPTURE_LEN, 1),
    ("TRIG_MODE", ADDR_TRIG_MODE, 0),
    ("TRIG_VALUE", ADDR_TRIG_VALUE, 0),
    ("TRIG_MASK", ADDR_TRIG_MASK, 0),
    ("DECIM", ADDR_DECIM, 0),
    ("COND_CFG", ADDR_COND_CFG, 0),
    ("COND_VAL", ADDR_COND_VAL, 0),
    ("QUAL_MODE", ADDR_QUAL_MODE, 0),
    ("QUAL_CFG", ADDR_QUAL_CFG, 0),
    ("QUAL_VAL", ADDR_QUAL_VAL, 0),
)

# Host capture: PRETRIG = 4, POSTTRIG = 4 around TRIG_VALUE 0x220.
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


async def _count_trigger_pulses(dut, box: list[int]) -> None:
    while True:
        await RisingEdge(dut.sample_clk_i)
        await ReadOnly()
        if int(dut.trigger_pulse_o.value) == 1:
            box[0] += 1


@cocotb.test()
@requires("REA-REQ-977")
async def test_rea_req_977_false_build_ignores_every_reset_generic(dut):
    """G_ARM_AT_RESET = false with every G_RESET_* non-zero: the core leaves
    reset disarmed and zeroed, ignores the reset trigger pattern, and a host
    capture behaves as before P2.32."""
    for sig in (dut.arst_i, dut.tck_i, dut.tdi_i, dut.capture_i,
                dut.shift_en_i, dut.update_i, dut.sel_i, dut.ext_trigger_i,
                dut.trigger_i):
        sig.value = 0
    dut.sample_rst_i.value = 1
    dut.probe_i.value = 0x055
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck_i, 25.0, unit="ns").start())
    await ClockCycles(dut.sample_clk_i, 6)
    dut.sample_rst_i.value = 0
    await ClockCycles(dut.sample_clk_i, 12)
    await ClockCycles(dut.tck_i, 6)
    pulses = [0]
    cocotb.start_soon(_count_trigger_pulses(dut, pulses))

    # 1. Out of reset: disarmed, STATUS[10] clear, zeroed configuration.
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status == 0, (
        f"REA-REQ-977: STATUS=0x{status:08X} out of reset with "
        "G_ARM_AT_RESET=false, want 0x00000000 (not armed, STATUS[10] clear)"
    )
    for name, addr, want in RESET_REGISTERS:
        got = await _jtag_read(dut, addr)
        assert got == want, (
            f"REA-REQ-977: {name} reads 0x{got:08X} with G_ARM_AT_RESET=false, "
            f"want 0x{want:08X} (G_RESET_{name} must be ignored)"
        )
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 0, (
        f"REA-REQ-977: CAPTURE_EPOCH reads {epoch} out of reset with "
        "G_ARM_AT_RESET=false, want 0"
    )

    # 2. The G_RESET_TRIG_VALUE pattern (0x124) must not fire a disarmed core.
    for step in range(64):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x100 + step
    await FallingEdge(dut.sample_clk_i)
    dut.probe_i.value = 0x055
    await ClockCycles(dut.sample_clk_i, 160)
    await ClockCycles(dut.tck_i, 6)
    assert pulses[0] == 0, (
        f"REA-REQ-977: {pulses[0]} trigger_pulse_o on the G_RESET_TRIG_VALUE "
        "pattern with G_ARM_AT_RESET=false, want 0"
    )
    status = await _jtag_read(dut, ADDR_STATUS)
    assert status == 0, (
        f"REA-REQ-977: STATUS=0x{status:08X} after the reset trigger pattern "
        "with G_ARM_AT_RESET=false, want 0x00000000"
    )

    # 3. A host-armed capture, as before P2.32.
    await _jtag_write(dut, ADDR_PRETRIG, 4)
    await _jtag_write(dut, ADDR_POSTTRIG, 4)
    await _jtag_write(dut, ADDR_TRIG_MODE, 0x01)
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0x220)
    await _jtag_write(dut, ADDR_TRIG_MASK, 0xFFF)
    await _jtag_write(dut, ADDR_CTRL, CTRL_ARM)
    await ClockCycles(dut.sample_clk_i, 16)
    for step in range(48):
        await FallingEdge(dut.sample_clk_i)
        dut.probe_i.value = 0x200 + step
    # Long enough for the capture, its CRC sweep and the publication settle.
    await ClockCycles(dut.sample_clk_i, 400)
    await ClockCycles(dut.tck_i, 6)

    assert pulses[0] == 1, (
        f"REA-REQ-977: host capture fired {pulses[0]} trigger_pulse_o, want 1"
    )
    status = await _jtag_read(dut, ADDR_STATUS)
    want = STATUS_TRIGGERED | STATUS_DONE | STATUS_CRC_VALID
    assert status == want, (
        f"REA-REQ-977: host capture STATUS=0x{status:08X}, want 0x{want:08X}"
    )
    epoch = await _jtag_read(dut, ADDR_CAPTURE_EPOCH)
    assert epoch == 1, (
        f"REA-REQ-977: CAPTURE_EPOCH reads {epoch} after one host arm, want 1"
    )
    start = await _jtag_read(dut, ADDR_START_PTR) & (DEPTH - 1)
    window = []
    for idx in range(len(EXPECTED_HOST_WINDOW)):
        addr = ADDR_DATA_BASE + 4 * ((start + idx) % DEPTH)
        window.append(await _jtag_read(dut, addr) & 0xFFF)
    assert window == EXPECTED_HOST_WINDOW, (
        f"REA-REQ-977: host window {window} != {EXPECTED_HOST_WINDOW}"
    )


if __name__ == "__main__":
    main()
