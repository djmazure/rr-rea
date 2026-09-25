# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.5 — PRETRIG_VALID after a long pre-trigger wait: exact below the
ring-overshoot bound, and never vouching for an overwritten cell above it
(REA-REQ-963, REA-REQ-960).

The host trusts the last PRETRIG_VALID pre-trigger cells (RTL-T1.16). The
samples stored while the trigger pipeline catches up land past the trigger
cell, so with PRETRIG near DEPTH they overwrite the OLDEST pre-trigger cells
(REA-T2.6). Measured on this bench (DEPTH 64, PRETRIG 62): 59 genuine cells
undecimated, while the pre-T2.5 FSM reported 60, one overwritten cell trusted.

REA-T2.5 moved the count off the fire path (Fmax: the since_arm - fire_lag
chain capped qualifier-less builds at 136-145 MHz after REA-T2.4). Contract:
  * PRETRIG <= DEPTH - 1 - C_PIPE_STAGES: PRETRIG_VALID is exact (= PRETRIG).
  * above it: PRETRIG_VALID <= the genuine cell count (safe; may under-count).

Oracle: a counter probe; every DPRAM write is recorded, the read-back window
is compared cell by cell with the stored sequence, and the genuine count is
the contiguous run of correct cells ending at the trigger.

Run via:  rr sim run test_rea_pretrig_valid_sat_t2_5
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

SAMPLE_W = 16
DEPTH = 64
QUAL_CONDS = 2
GENERICS = {"G_SAMPLE_W": SAMPLE_W, "G_DEPTH": DEPTH, "G_QUAL_CONDS": QUAL_CONDS}
_RTL_DIR = _Path(__file__).resolve().parents[3] / "rtl"
_FIX = _Path(__file__).resolve().parent / "fixtures"

PTR_MASK = DEPTH - 1
TRIG_EQ_MODE = 1
# Trigger-pipeline latency for these generics (C_PIPE_STAGES): the measured
# ring overshoot of an undecimated capture, DEPTH - 1 - 59.
PIPE_STAGES = 4
# The deepest pre-trigger window whose ring keeps a spare cell (window =
# DEPTH - 1). A window of exactly DEPTH cells is REA-T2.6's bench.
PRETRIG, POSTTRIG = DEPTH - 2, 0

INSTANCES = {
    "zero": ("zero_we_o", "zero_addr_o", "zero_din_o",
             "zero_start_ptr_o", "zero_pretrig_valid_o", "zero_status_o"),
    "off": ("off_we_o", "off_addr_o", "off_din_o",
            "off_start_ptr_o", "off_pretrig_valid_o", "off_status_o"),
    "qual": ("dpram_we_o", "dpram_addr_o", "dpram_din_o",
             "start_ptr_o", "pretrig_valid_o", "status_o"),
}
# REA-P2.11 (rea_store_lag): u_off / u_qual elaborate a qualifier, so they
# store each sample one cycle after it is on probe_i; u_ref / u_zero do not.
STORE_LAG = {"ref": 0, "zero": 0, "off": 1, "qual": 1}
STATUS_DONE = 1 << 2


def main() -> None:
    run_simulation(
        top_level="rr_rea_qual_lockstep_harness",
        module="test_rea_pretrig_valid_sat_t2_5",
        custom_libraries={
            "work": [
                str(_RTL_DIR / "rr_rea_pkg.vhd"),
                str(_FIX / "rr_rea_capture_fsm_ref_v09.vhd"),
                str(_RTL_DIR / "rr_rea_capture_fsm.vhd"),
                str(_FIX / "rr_rea_qual_lockstep_harness.vhd"),
            ],
        },
        generics=GENERICS,
        waves=False,
        simulator="nvc",
    )


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.mem = {name: {} for name in INSTANCES}
        self.stores = {name: [] for name in INSTANCES}

    async def run(self) -> None:
        dut = self.dut
        while True:
            dut.probe_i.value = self.cycle & 0xFFFF
            await ReadOnly()
            for name, (we, addr, din, *_rest) in INSTANCES.items():
                if int(getattr(dut, we).value):
                    value = int(getattr(dut, din).value)
                    self.mem[name][int(getattr(dut, addr).value)] = value
                    # Labelled with the cycle the SAMPLE was on probe_i:
                    # a qualifier build writes it STORE_LAG cycles later.
                    self.stores[name].append((self.cycle - STORE_LAG[name],
                                              value))
            await RisingEdge(dut.sample_clk_i)
            self.cycle += 1


async def _start(dut) -> Bench:
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    for sig in ("probe_i", "arm_pulse_i", "reset_pulse_i", "trigger_i", "pretrig_len_i",
                "posttrig_len_i", "trig_value_i", "trig_mask_i", "trig_mode_i",
                "decim_ratio_i", "qual_enable_i", "qual_or_i", "qual_values_i",
                "qual_masks_i", "qual_ops_i", "qual_valid_i"):
        getattr(dut, sig).value = 0
    dut.sample_rst_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    bench = Bench(dut)
    cocotb.start_soon(bench.run())
    for _ in range(2):
        await RisingEdge(dut.sample_clk_i)
    return bench


async def _capture(bench: Bench, decim: int, trig_offset: int,
                   pretrig: int = PRETRIG, posttrig: int = POSTTRIG) -> dict:
    dut = bench.dut
    dut.decim_ratio_i.value = decim
    dut.pretrig_len_i.value = pretrig
    dut.posttrig_len_i.value = posttrig
    dut.trig_mode_i.value = TRIG_EQ_MODE
    dut.trig_mask_i.value = 0xFFFF
    await ReadOnly()
    arm_cycle = int(dut.probe_i.value) + 1
    await RisingEdge(dut.sample_clk_i)
    trig_value = (arm_cycle + trig_offset) & 0xFFFF
    dut.trig_value_i.value = trig_value
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0

    done = {}
    for since_arm in range(trig_offset + 40 * (decim + 1) * (posttrig + 8)):
        await ReadOnly()
        for name, (*_w, start, pvalid, status) in INSTANCES.items():
            # A lagged instance takes the arm STORE_LAG cycles late, so its
            # status still shows the PREVIOUS capture until then.
            if since_arm < STORE_LAG[name]:
                continue
            if name not in done and int(getattr(dut, status).value) & STATUS_DONE:
                done[name] = (int(getattr(dut, start).value),
                              int(getattr(dut, pvalid).value))
        if len(done) == len(INSTANCES):
            break
        await RisingEdge(dut.sample_clk_i)
    assert len(done) == len(INSTANCES), (
        f"DECIM={decim}: never done: {sorted(set(INSTANCES) - set(done))}")
    for _ in range(4):
        await RisingEdge(dut.sample_clk_i)
    return {"arm_cycle": arm_cycle, "trig_value": trig_value, "done": done,
            "pretrig": pretrig, "posttrig": posttrig}


def _judge(bench: Bench, name: str, decim: int, cap: dict,
           full_window: bool = False) -> list:
    errors = []
    stores = bench.stores[name]
    arm_cycle, trig_value = cap["arm_cycle"], cap["trig_value"]
    pretrig, posttrig = cap["pretrig"], cap["posttrig"]
    idx = next((i for i, (c, v) in enumerate(stores)
                if c > arm_cycle and v >= trig_value), None)
    if idx is None:
        return ["no sample stored at or after the trigger value"]
    pre_stores = sum(1 for c, _v in stores[:idx] if c > arm_cycle)
    want_pvalid = min(pre_stores, pretrig)
    start, pvalid = cap["done"][name]
    window = pretrig + posttrig + 1
    trig_cell = stores[idx][1]
    want = [(trig_cell + (i - pretrig) * (decim + 1)) & 0xFFFF
            for i in range(window)]
    mem = bench.mem[name]
    got = [mem.get((start + i) & PTR_MASK) for i in range(window)]
    # Genuine pre-trigger cells: the contiguous run ending at the trigger.
    genuine = 0
    for i in range(pretrig - 1, -1, -1):
        if got[i] != want[i]:
            break
        genuine += 1
    genuine = min(genuine, want_pvalid)
    # The host trusts the last `pvalid` pre-trigger cells: every one of them
    # must be genuine (a stale cell read as context is the T1.16 failure).
    # Where nothing was overwritten it must also be exact.
    exact = pretrig <= DEPTH - 1 - PIPE_STAGES
    if pvalid > genuine or (exact and pvalid != want_pvalid):
        errors.append(f"pretrig_valid_o={pvalid}, genuine pre-trigger cells="
                      f"{genuine} (stored before trigger={pre_stores}, "
                      f"PRETRIG={pretrig}, DEPTH={DEPTH})")
    for i in range(0 if full_window else pretrig, window):
        if got[i] != want[i]:
            errors.append(f"cell {i}: read {got[i]}, want {want[i]}")
            break
    return errors


@cocotb.test()
@requires("REA-REQ-963", "REA-REQ-960")
async def test_rea_t2_5_pretrig_valid_after_a_long_wait(dut):
    """Trigger after ~4 DEPTHs of stores, DECIM 0..3 at every phase, POSTTRIG
    0: at PRETRIG = DEPTH-1-C_PIPE_STAGES every instance reports exactly
    PRETRIG; at PRETRIG = DEPTH-2 it never exceeds the genuine cell count."""
    bench = await _start(dut)
    failures = []
    for pretrig in (DEPTH - 1 - PIPE_STAGES, DEPTH - 2):
        for decim in range(0, 4):
            for phase in range(decim + 1):
                cap = await _capture(bench, decim,
                                     4 * DEPTH * (decim + 1) + phase,
                                     pretrig=pretrig, posttrig=0)
                for name in INSTANCES:
                    for err in _judge(bench, name, decim, cap):
                        failures.append(f"u_{name} PRETRIG={pretrig} "
                                        f"DECIM={decim} phase={phase}: {err}")
    assert not failures, (f"{len(failures)} defect(s):\n  "
                          + "\n  ".join(failures[:24]))


if __name__ == "__main__":
    main()
