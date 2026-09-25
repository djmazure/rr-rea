# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.4 — a decimated capture's window is complete and belongs to it
(REA-REQ-501, REA-REQ-960).

With DECIM=N>0 the capture FSM used to stop one stored cell short whenever the
trigger's fire edge fell on a non-tick cycle: `done` rose before the last
post-trigger cell was written, so the host read back a stale cell from an
earlier capture as the final sample. And the pre-trigger count subtracted the
trigger pipeline's CYCLES from a count of STORED samples, understating
`pretrig_valid_o` for an early trigger.

Oracle: a counter probe, so every stored value names the cycle it was taken
on. The testbench records each instance's DPRAM writes from `*_we_o`/
`*_addr_o`/`*_din_o` into its own memory model, checks the decimation grid
(stores exactly N+1 apart, REQ-501), and derives the expected window from the
stored sequence and the trigger value: the first stored sample at or after the
trigger value is the trigger cell, PRETRIG stored samples before it and
POSTTRIG after it. Read back from `start_ptr_o` like the host does, every
cell of this capture must match, including the last one.

Toplevel is the REA-P2.7 lockstep harness, so the same check covers a core
elaborated without the qualifier (`u_zero`), a qualified core with QUAL_MODE
off (`u_off`) and the qualified core with its qualifier disabled (`u_qual`).

Run via:  rr sim run test_rea_decim_window_t2_4
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
TRIG_EQ_MODE = 1  # TRIG_MODE[0]=enable, op nibble [7:4] = EQ (0)
PRETRIG, POSTTRIG = 4, 5

# The three current-FSM instances; u_ref is the frozen pre-P2.7 oracle and is
# not judged here.
INSTANCES = {
    "zero": ("zero_we_o", "zero_addr_o", "zero_din_o",
             "zero_start_ptr_o", "zero_pretrig_valid_o", "zero_status_o"),
    "off": ("off_we_o", "off_addr_o", "off_din_o",
            "off_start_ptr_o", "off_pretrig_valid_o", "off_status_o"),
    "qual": ("dpram_we_o", "dpram_addr_o", "dpram_din_o",
             "start_ptr_o", "pretrig_valid_o", "status_o"),
}
STATUS_DONE = 1 << 2


def main() -> None:
    run_simulation(
        top_level="rr_rea_qual_lockstep_harness",
        module="test_rea_decim_window_t2_4",
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
    """Counter probe + one memory model and store log per instance."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.mem = {name: {} for name in INSTANCES}
        self.stores = {name: [] for name in INSTANCES}  # (cycle, value)

    async def run(self) -> None:
        dut = self.dut
        while True:
            dut.probe_i.value = self.cycle & 0xFFFF
            await ReadOnly()
            for name, (we, addr, din, *_rest) in INSTANCES.items():
                if int(getattr(dut, we).value):
                    value = int(getattr(dut, din).value)
                    self.mem[name][int(getattr(dut, addr).value)] = value
                    self.stores[name].append((self.cycle, value))
            await RisingEdge(dut.sample_clk_i)
            self.cycle += 1

    async def cycles(self, n: int) -> None:
        for _ in range(n):
            await RisingEdge(self.dut.sample_clk_i)


def _idle(dut) -> None:
    for sig in ("arm_pulse_i", "reset_pulse_i", "trigger_i", "pretrig_len_i",
                "posttrig_len_i", "trig_value_i", "trig_mask_i", "trig_mode_i",
                "decim_ratio_i", "qual_enable_i", "qual_or_i", "qual_values_i",
                "qual_masks_i", "qual_ops_i", "qual_valid_i"):
        getattr(dut, sig).value = 0


async def _start(dut) -> Bench:
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    _idle(dut)
    dut.sample_rst_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    bench = Bench(dut)
    cocotb.start_soon(bench.run())
    await bench.cycles(2)
    return bench


async def _capture(bench: Bench, decim: int, trig_offset: int) -> dict:
    """Arm, trigger on the counter value `trig_offset` cycles after the arm
    pulse, wait for every instance's done. Returns per-instance results."""
    dut = bench.dut
    dut.decim_ratio_i.value = decim
    dut.pretrig_len_i.value = PRETRIG
    dut.posttrig_len_i.value = POSTTRIG
    dut.trig_mode_i.value = TRIG_EQ_MODE
    dut.trig_mask_i.value = 0xFFFF
    # Anchor on the probe the DUT actually sees (the counter IS the cycle
    # label): this cycle presents k, so the arm cycle presents k + 1. Reading
    # the bench's cycle counter here would race its own RisingEdge wake-up.
    await ReadOnly()
    arm_cycle = int(dut.probe_i.value) + 1
    await RisingEdge(dut.sample_clk_i)
    trig_value = (arm_cycle + trig_offset) & 0xFFFF
    dut.trig_value_i.value = trig_value
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0

    done = {}
    for _ in range(40 * (decim + 1) * (PRETRIG + POSTTRIG + 4) + trig_offset):
        await ReadOnly()
        for name, (*_w, start, pvalid, status) in INSTANCES.items():
            if name not in done and int(getattr(dut, status).value) & STATUS_DONE:
                done[name] = (int(getattr(dut, start).value),
                              int(getattr(dut, pvalid).value))
        if len(done) == len(INSTANCES):
            break
        await RisingEdge(dut.sample_clk_i)
    assert len(done) == len(INSTANCES), (
        f"DECIM={decim} offset={trig_offset}: never done: "
        f"{sorted(set(INSTANCES) - set(done))}")
    # A few more cycles: a write racing done must not be missed by the model.
    await bench.cycles(4)
    return {"arm_cycle": arm_cycle, "trig_value": trig_value, "done": done}


def _judge(bench: Bench, name: str, decim: int, cap: dict) -> list:
    """Every mismatch between instance `name`'s read-back window and the
    window the stored sequence says it must be."""
    errors = []
    stores = bench.stores[name]
    arm_cycle, trig_value = cap["arm_cycle"], cap["trig_value"]

    # REQ-501: after the arm, stores land exactly DECIM+1 cycles apart.
    after_arm = [(c, v) for c, v in stores if c > arm_cycle]
    gaps = {b[0] - a[0] for a, b in zip(after_arm, after_arm[1:])}
    if gaps and gaps != {decim + 1}:
        rel = [c - arm_cycle for c, _v in after_arm[:6]]
        errors.append(f"store spacing {sorted(gaps)}, want {{{decim + 1}}} "
                      f"(first stores at arm+{rel})")

    # The trigger cell: the first sample stored at or after the trigger value.
    idx = next((i for i, (c, v) in enumerate(stores)
                if c > arm_cycle and v >= trig_value), None)
    if idx is None:
        return errors + ["no sample stored at or after the trigger value"]
    pre_stores = sum(1 for c, _v in stores[:idx] if c > arm_cycle)
    want_pvalid = min(pre_stores, PRETRIG)

    start, pvalid = cap["done"][name]
    if pvalid != want_pvalid:
        errors.append(f"pretrig_valid_o={pvalid}, want {want_pvalid} "
                      f"({pre_stores} samples stored after the arm, before "
                      f"the trigger cell)")

    # Counter probe on a stride-(DECIM+1) grid: cell i holds the trigger
    # cell's value + (i - PRETRIG) * (DECIM+1) — derived from the spec, not
    # from which samples the DUT happened to store.
    window = PRETRIG + POSTTRIG + 1
    trig_cell = stores[idx][1]
    want = [(trig_cell + (i - PRETRIG) * (decim + 1)) & 0xFFFF
            for i in range(window)]
    mem = bench.mem[name]
    got = [mem.get((start + i) & PTR_MASK) for i in range(window)]
    # Cells before this arm's pre-trigger context are residue the host masks
    # with pretrig_valid; judge from the first cell of THIS capture.
    for i in range(PRETRIG - want_pvalid, window):
        if got[i] != want[i]:
            errors.append(f"cell {i}: read {got[i]}, want {want[i]}")
    return errors


async def _sweep(dut, offsets) -> None:
    bench = await _start(dut)
    failures = []
    runs = 0
    for decim in range(1, 7):
        for offset in offsets(decim):
            cap = await _capture(bench, decim, offset)
            runs += 1
            for name in INSTANCES:
                for err in _judge(bench, name, decim, cap):
                    failures.append(f"u_{name} DECIM={decim} trig@arm+{offset}: {err}")
    dut._log.info(f"{runs} decimated captures judged on {len(INSTANCES)} instances")
    assert not failures, (f"{len(failures)} window defect(s):\n  "
                          + "\n  ".join(failures[:40]))


@cocotb.test()
@requires("REA-REQ-501", "REA-REQ-960")
async def test_rea_t2_4_decimated_window_ends_on_its_own_last_cell(dut):
    """DECIM 1..6 x every trigger phase 0..DECIM, trigger well after the
    pre-trigger window has filled: every cell read back from `start_ptr_o`
    for PRETRIG+POSTTRIG+1 cells, INCLUDING the last post-trigger cell, is the
    sample this capture stored there (never a stale cell from the previous
    capture), and `pretrig_valid_o` = PRETRIG."""
    await _sweep(dut, lambda d: [(PRETRIG + 3) * (d + 1) + p
                                 for p in range(d + 1)])


@cocotb.test()
@requires("REA-REQ-960")
async def test_rea_t2_4_early_trigger_pretrig_valid_counts_stored_samples(dut):
    """DECIM 1..6, trigger 1..2*(DECIM+1)+1 cycles after the arm (the arm
    cycle's own sample precedes `armed` and never fires), before the
    pre-trigger window can fill: `pretrig_valid_o` equals the number of
    samples actually STORED after the arm and before the trigger cell (not
    that count minus the trigger pipeline's cycles), and the window from
    that first valid cell to the last post-trigger cell matches."""
    await _sweep(dut, lambda d: list(range(1, 2 * (d + 1) + 2)))


if __name__ == "__main__":
    main()
