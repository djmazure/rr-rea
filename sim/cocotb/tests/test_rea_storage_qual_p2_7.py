# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.7 — storage qualification at the capture FSM (REA-REQ-950..955, 959).

Toplevel is ``rr_rea_qual_lockstep_harness``: the FROZEN pre-qualification FSM
(``u_ref``) beside three current FSMs sharing one stimulus. The lockstep test
holds the new FSM to today's analyzer cycle for cycle with qualification off;
the qualification tests read ``u_qual`` and compare it against an independent
Python model of the qualifier built from SPEC.md, never from DUT state.

Run via:  rr sim run test_rea_storage_qual_p2_7
"""

from __future__ import annotations

import random
import sys as _sys
from dataclasses import dataclass, field
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

MASK_W = (1 << SAMPLE_W) - 1
PTR_MASK = DEPTH - 1

# C_TRIG_OP_* (rr_rea_pkg), shared by trigger and qualifier encodings.
OP_EQ, OP_NE, OP_LT, OP_GT, OP_RISE, OP_FALL = 0, 1, 2, 3, 4, 5

INSTANCES = ("ref", "zero", "off", "qual")
OUTPUTS = ("we", "addr", "din", "wr_ptr", "trig_ptr", "start_ptr",
           "pretrig_valid", "status")


def main() -> None:
    run_simulation(
        top_level="rr_rea_qual_lockstep_harness",
        module="test_rea_storage_qual_p2_7",
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


# ── Stimulus plumbing ────────────────────────────────────────────────


@dataclass
class Slot:
    """One qualifier slot, as the regbank expands it: full-width value/mask."""
    op: int
    lsb: int
    width: int
    value: int
    valid: bool = True

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb

    @property
    def shifted_value(self) -> int:
        return (self.value << self.lsb) & self.mask


@dataclass
class QualCfg:
    enable: bool = False
    use_or: bool = False
    slots: list = field(default_factory=list)


def qualifier_holds(cfg: QualCfg, probe: int, prev: int) -> bool:
    """SPEC.md "Storage qualification" model — the oracle for REQ-951/952/953."""
    if not cfg.enable:
        return True
    valid = [s for s in cfg.slots if s.valid]
    if not valid:
        return True  # REQ-952: enabled but empty stores everything

    def one(s: Slot) -> bool:
        m = s.mask
        if s.op == OP_EQ:
            return (probe & m) == s.shifted_value
        if s.op == OP_NE:
            return (probe & m) != s.shifted_value
        if s.op == OP_RISE:
            return bool(probe & ~prev & m)
        if s.op == OP_FALL:
            return bool(~probe & prev & m)
        return False  # REQ-953: LT/GT/other never qualify

    hits = [one(s) for s in valid]
    return any(hits) if cfg.use_or else all(hits)


def _apply_qual(dut, cfg: QualCfg) -> None:
    values = masks = ops = valid = 0
    for k in range(QUAL_CONDS):
        s = cfg.slots[k] if k < len(cfg.slots) else None
        if s is None:
            continue
        values |= s.shifted_value << (k * SAMPLE_W)
        masks |= s.mask << (k * SAMPLE_W)
        ops |= (s.op & 0xF) << (k * 4)
        valid |= int(s.valid) << k
    dut.qual_enable_i.value = int(cfg.enable)
    dut.qual_or_i.value = int(cfg.use_or)
    dut.qual_values_i.value = values
    dut.qual_masks_i.value = masks
    dut.qual_ops_i.value = ops
    dut.qual_valid_i.value = valid


def _idle_inputs(dut) -> None:
    dut.probe_i.value = 0
    dut.arm_pulse_i.value = 0
    dut.reset_pulse_i.value = 0
    dut.trigger_i.value = 0
    dut.pretrig_len_i.value = 0
    dut.posttrig_len_i.value = 0
    dut.trig_value_i.value = 0
    dut.trig_mask_i.value = 0
    dut.trig_mode_i.value = 0
    dut.decim_ratio_i.value = 0
    _apply_qual(dut, QualCfg())


async def _start(dut) -> None:
    cocotb.start_soon(Clock(dut.sample_clk_i, 8.0, unit="ns").start())
    _idle_inputs(dut)
    dut.sample_rst_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    await RisingEdge(dut.sample_clk_i)


_QUAL_PORTS = {"we": "dpram_we_o", "addr": "dpram_addr_o", "din": "dpram_din_o",
               "wr_ptr": "wr_ptr_o", "trig_ptr": "trig_ptr_o",
               "start_ptr": "start_ptr_o", "pretrig_valid": "pretrig_valid_o",
               "status": "status_o"}


def _read(dut, inst: str) -> dict:
    """One instance's outputs. u_qual keeps the FSM's own port names
    (`dpram_we_o`, `trig_ptr_o`, ...); the lockstep peers are prefixed."""
    if inst == "qual":
        return {name: int(getattr(dut, port).value)
                for name, port in _QUAL_PORTS.items()}
    return {name: int(getattr(dut, f"{inst}_{name}_o").value) for name in OUTPUTS}


# Trigger that can never fire: NE against itself under a zero mask is always
# "equal", so NE never matches.
NEVER_TRIGGER_MODE = 1 | (OP_NE << 4)


# ── REQ-950: qualification off is today's analyzer, bit for bit ─────


@cocotb.test()
@requires("REA-REQ-950")
async def test_rea_req_950_qualification_off_is_bit_exact_lockstep(dut):
    """`dpram_we_o`/`dpram_addr_o`/`dpram_din_o`/`trig_ptr_o`/`start_ptr_o`/
    `pretrig_valid_o` and status of u_zero and u_off equal the frozen u_ref on
    every cycle, under random probes, arms, soft resets, external triggers,
    decimation, overflowing windows and LIVE junk in the qualifier slots."""
    rng = random.Random(0x950)
    await _start(dut)

    cycles = 30_000
    dones = overflows = decimated_dones = 0
    decim = 0
    prev_done = 0
    for cyc in range(cycles):
        # Config changes are arm-time; change them freely, they only bite on arm.
        if rng.random() < 0.01:
            dut.pretrig_len_i.value = rng.randrange(DEPTH)
            dut.posttrig_len_i.value = rng.randrange(DEPTH)
            decim = rng.choice([0, 0, 0, 1, 2, 5])
            dut.decim_ratio_i.value = decim
            dut.trig_mode_i.value = 1 | (rng.choice([OP_EQ, OP_NE, OP_LT, OP_GT,
                                                      OP_RISE, OP_FALL]) << 4)
            dut.trig_value_i.value = rng.getrandbits(SAMPLE_W)
            dut.trig_mask_i.value = rng.choice([0x000F, 0x00F0, 0x0003, 0x8001])
            _apply_qual(dut, QualCfg(
                enable=bool(rng.getrandbits(1)),
                use_or=bool(rng.getrandbits(1)),
                slots=[Slot(op=rng.randrange(16), lsb=rng.randrange(8),
                            width=rng.randrange(1, 8),
                            value=rng.getrandbits(8),
                            valid=bool(rng.getrandbits(1)))
                       for _ in range(QUAL_CONDS)]))
        dut.probe_i.value = rng.getrandbits(SAMPLE_W)
        dut.arm_pulse_i.value = int(rng.random() < 0.004)
        dut.reset_pulse_i.value = int(rng.random() < 0.0005)
        dut.trigger_i.value = int(rng.random() < 0.0008)
        await ReadOnly()
        ref = _read(dut, "ref")
        for inst in ("zero", "off"):
            got = _read(dut, inst)
            assert got == ref, (
                f"cycle {cyc}: u_{inst} diverged from the frozen v0.9 FSM with "
                f"qualification off: {inst}={got} ref={ref}")
        done = (ref["status"] >> 2) & 1
        if done and not prev_done:
            dones += 1
            decimated_dones += int(decim > 0)
        overflows += (ref["status"] >> 3) & 1
        prev_done = done
        await RisingEdge(dut.sample_clk_i)

    # The stimulus must actually have exercised captures, or lockstep is vacuous.
    assert dones >= 20, f"only {dones} captures completed — stimulus too weak"
    assert decimated_dones >= 3, f"only {decimated_dones} decimated captures"
    assert overflows > 0, "no overflowing window configuration was exercised"
    dut._log.info(f"lockstep: {cycles} cycles, {dones} captures "
                  f"({decimated_dones} decimated), overflow cycles {overflows}")


# ── REQ-951/952/953/959: the qualifier gates the store, per cycle ────


async def _arm_free_running(dut, cfg: QualCfg) -> None:
    """Latch `cfg` with an arm whose trigger can never fire, so the FSM stays
    armed and storing and every cycle's `dpram_we_o` is the qualifier."""
    _apply_qual(dut, cfg)
    dut.trig_mode_i.value = NEVER_TRIGGER_MODE
    dut.trig_mask_i.value = 0
    dut.pretrig_len_i.value = 4
    dut.posttrig_len_i.value = 4
    dut.decim_ratio_i.value = 0
    dut.arm_pulse_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0


async def _check_store_gate(dut, rng, cfg: QualCfg, latched: QualCfg,
                            cycles: int, label: str) -> int:
    """Drive random probes; assert `dpram_we_o` == model(latched) each cycle and
    `wr_ptr_o` advances by exactly one on a store cycle and holds otherwise.
    Returns stores seen."""
    prev = int(dut.probe_i.value)
    stores = 0
    last_ptr = last_we = None
    for cyc in range(cycles):
        probe = rng.getrandbits(SAMPLE_W)
        dut.probe_i.value = probe
        await ReadOnly()
        ptr = int(dut.wr_ptr_o.value)
        if last_ptr is not None:
            assert ptr == (last_ptr + last_we) & PTR_MASK, (
                f"[{label}] cycle {cyc}: wr_ptr_o={ptr}, but the previous "
                f"cycle's wr_ptr_o={last_ptr} with dpram_we_o={last_we}")
        want = qualifier_holds(latched, probe, prev)
        got = int(dut.dpram_we_o.value)
        assert got == int(want), (
            f"[{label}] cycle {cyc}: dpram_we_o={got}, qualifier model says "
            f"{int(want)} for probe=0x{probe:04X} prev=0x{prev:04X}")
        if got:
            assert int(dut.dpram_din_o.value) == probe
            assert int(dut.dpram_addr_o.value) == ptr
            stores += 1
        last_ptr, last_we = ptr, got
        prev = probe
        await RisingEdge(dut.sample_clk_i)
    return stores


@cocotb.test()
@requires("REA-REQ-951")
@requires("REA-REQ-952")
async def test_rea_req_951_952_qualifier_gates_store_every_op_and_combine(dut):
    """Per cycle, `dpram_we_o` holds iff the qualifier holds, for EQ/NE/RISE/
    FALL, AND and OR, single and double slots, and the empty qualifier."""
    rng = random.Random(0x951)
    await _start(dut)
    cases = [
        QualCfg(True, False, [Slot(OP_EQ, 0, 1, 1)]),
        QualCfg(True, False, [Slot(OP_NE, 0, 2, 0)]),
        QualCfg(True, False, [Slot(OP_RISE, 3, 2, 0)]),
        QualCfg(True, False, [Slot(OP_FALL, 7, 1, 0)]),
        QualCfg(True, False, [Slot(OP_EQ, 0, 1, 1), Slot(OP_EQ, 4, 4, 0xA)]),
        QualCfg(True, True, [Slot(OP_EQ, 0, 1, 1), Slot(OP_EQ, 4, 4, 0xA)]),
        QualCfg(True, True, [Slot(OP_RISE, 1, 1, 0), Slot(OP_FALL, 1, 1, 0)]),
        QualCfg(True, False, [Slot(OP_EQ, 0, 3, 5, valid=False)]),  # empty
        QualCfg(True, True, []),                                   # empty OR
    ]
    for i, cfg in enumerate(cases):
        await _arm_free_running(dut, cfg)
        stores = await _check_store_gate(dut, rng, cfg, cfg, 600, f"case {i}")
        # A qualifier that stores everything or nothing proves nothing here.
        empty = not [s for s in cfg.slots if s.valid]
        if empty:
            assert stores == 600, f"case {i}: empty qualifier must store all"
        else:
            assert 0 < stores < 600, f"case {i}: degenerate stimulus ({stores})"


@cocotb.test()
@requires("REA-REQ-953")
async def test_rea_req_953_lt_gt_slots_never_qualify(dut):
    """An LT or GT slot never qualifies (`dpram_we_o` stays low under AND) and
    is ignored under OR."""
    rng = random.Random(0x953)
    await _start(dut)
    for op in (OP_LT, OP_GT, 9, 15):
        cfg = QualCfg(True, False, [Slot(op, 0, 8, 0x80)])
        await _arm_free_running(dut, cfg)
        assert await _check_store_gate(dut, rng, cfg, cfg, 300,
                                       f"AND op {op}") == 0
        cfg_or = QualCfg(True, True, [Slot(op, 0, 8, 0x80), Slot(OP_EQ, 0, 1, 1)])
        await _arm_free_running(dut, cfg_or)
        stores = await _check_store_gate(dut, rng, cfg_or, cfg_or, 300,
                                         f"OR op {op}")
        assert 0 < stores < 300


@cocotb.test()
@requires("REA-REQ-959")
async def test_rea_req_959_qualifier_config_is_latched_on_arm(dut):
    """Rewriting the qualifier inputs while armed does not change what this
    capture stores: `dpram_we_o` follows the configuration latched at arm."""
    rng = random.Random(0x959)
    await _start(dut)
    latched = QualCfg(True, False, [Slot(OP_EQ, 0, 1, 1)])
    await _arm_free_running(dut, latched)
    # Scribble a different, live configuration without re-arming.
    scribble = QualCfg(True, True, [Slot(OP_NE, 0, 16, 0), Slot(OP_RISE, 2, 3, 0)])
    _apply_qual(dut, scribble)
    await _check_store_gate(dut, rng, scribble, latched, 500, "post-arm scribble")
    # Before any arm the reset configuration (qualification off) stores all.
    dut.sample_rst_i.value = 1
    await RisingEdge(dut.sample_clk_i)
    dut.sample_rst_i.value = 0
    await RisingEdge(dut.sample_clk_i)
    _apply_qual(dut, latched)
    assert await _check_store_gate(dut, rng, latched, QualCfg(), 200,
                                   "never armed") == 200


# ── REQ-954/955: sparse events, windows in stored samples ────────────


class Ring:
    """The DPRAM, reconstructed from `dpram_we_o`/`dpram_addr_o`/`dpram_din_o`."""

    def __init__(self) -> None:
        self.mem: dict = {}

    def snoop(self, dut) -> None:
        if int(dut.dpram_we_o.value):
            self.mem[int(dut.dpram_addr_o.value)] = int(dut.dpram_din_o.value)

    def window(self, start: int, n: int) -> list:
        return [self.mem.get((start + i) & PTR_MASK) for i in range(n)]


def _event_word(n: int) -> int:
    """Event n: flag bit 0 set, the event number in bits [15:1]."""
    return ((n << 1) | 1) & MASK_W


async def _sparse_capture(dut, rng, *, pretrig: int, posttrig: int,
                          trig_event: int | None, arm_after: int,
                          decim: int = 0, ext_trigger_gap: int | None = None,
                          events: int = 60, gap=(900, 1100)):
    """Events 1..`events` every ~1000 cycles, junk (flag 0) in between. The
    qualifier stores flag==1 only. Trigger on event `trig_event`'s exact word,
    or (ext_trigger_gap) pulse `trigger_i` inside the idle gap after that many
    events. Arms after `arm_after` events. Returns (ring, status snapshot at
    done, events stored after arm by done, done_event)."""
    ring = Ring()
    cfg = QualCfg(True, False, [Slot(OP_EQ, 0, 1, 1)])
    _apply_qual(dut, cfg)
    dut.pretrig_len_i.value = pretrig
    dut.posttrig_len_i.value = posttrig
    dut.decim_ratio_i.value = decim
    if trig_event is not None:
        dut.trig_mode_i.value = 1 | (OP_EQ << 4)
        dut.trig_value_i.value = _event_word(trig_event)
        dut.trig_mask_i.value = MASK_W
    else:
        dut.trig_mode_i.value = NEVER_TRIGGER_MODE
        dut.trig_mask_i.value = 0

    done_at = None
    last_event = 0
    for n in range(1, events + 1):
        idle = rng.randint(*gap)
        for i in range(idle):
            dut.probe_i.value = rng.getrandbits(SAMPLE_W) & ~1  # flag 0: junk
            dut.arm_pulse_i.value = int(n == arm_after + 1 and i == 5)
            dut.trigger_i.value = int(ext_trigger_gap is not None
                                      and n == ext_trigger_gap + 1 and i == idle // 2)
            await ReadOnly()
            ring.snoop(dut)
            if (int(dut.status_o.value) >> 2) & 1:
                done_at = last_event
                break
            await RisingEdge(dut.sample_clk_i)
        if done_at is not None:
            break
        dut.arm_pulse_i.value = 0
        dut.trigger_i.value = 0
        dut.probe_i.value = _event_word(n)
        await ReadOnly()
        ring.snoop(dut)
        last_event = n
        await RisingEdge(dut.sample_clk_i)
    if done_at is None:
        await ReadOnly()
    # The trigger-position outputs, read straight off u_qual's FSM ports.
    snap = {"status": int(dut.status_o.value),
            "trig_ptr": int(dut.trig_ptr_o.value),
            "start_ptr": int(dut.start_ptr_o.value),
            "pretrig_valid": int(dut.pretrig_valid_o.value)}
    await RisingEdge(dut.sample_clk_i)
    dut.arm_pulse_i.value = 0
    dut.trigger_i.value = 0
    return ring, snap, done_at


@cocotb.test()
@requires("REA-REQ-954")
@requires("REA-REQ-955")
async def test_rea_req_954_955_sparse_events_fill_the_window(dut):
    """1 event in ~1000 cycles, qualify=event: the window holds exactly the
    events (no idle cycle), the trigger event sits at index PRETRIG, POSTTRIG
    events follow it, and `pretrig_valid_o` is exact."""
    rng = random.Random(0x954)
    await _start(dut)
    pretrig, posttrig, trig = 10, 9, 25
    ring, snap, done_at = await _sparse_capture(
        dut, rng, pretrig=pretrig, posttrig=posttrig, trig_event=trig,
        arm_after=3)
    assert (snap["status"] >> 2) & 1, "sparse capture never completed"
    window = ring.window(snap["start_ptr"], pretrig + posttrig + 1)
    want = [_event_word(e) for e in range(trig - pretrig, trig + posttrig + 1)]
    assert window == want, (
        "qualified window is not exactly the events around the trigger:\n"
        f"  got  {[hex(w) if w is not None else None for w in window]}\n"
        f"  want {[hex(w) for w in want]}")
    assert snap["trig_ptr"] == (snap["start_ptr"] + pretrig) & PTR_MASK
    assert snap["pretrig_valid"] == pretrig
    assert done_at == trig + posttrig, (
        f"`done_o` rose after event {done_at}, want right after event "
        f"{trig + posttrig}: the window closes on its last stored sample, it "
        "does not wait for the next qualifying cycle (a silent bus would "
        "otherwise never finish the capture)")


@cocotb.test()
@requires("REA-REQ-955")
async def test_rea_req_955_pretrig_valid_counts_stored_samples_exactly(dut):
    """Armed after event 3, triggered on event 8 with PRETRIG=10: only events
    4..7 were stored after the arm, so `pretrig_valid_o` reads exactly 4."""
    rng = random.Random(0x955)
    await _start(dut)
    _ring, snap, _ = await _sparse_capture(
        dut, rng, pretrig=10, posttrig=3, trig_event=8, arm_after=3, events=20)
    assert (snap["status"] >> 2) & 1
    assert snap["pretrig_valid"] == 4, (
        f"pretrig_valid_o={snap['pretrig_valid']}, but exactly 4 qualified "
        "samples (events 4..7) were stored after the arm before the trigger")


@cocotb.test()
@requires("REA-REQ-955")
async def test_rea_req_955_unqualified_trigger_lands_on_next_stored_sample(dut):
    """An external trigger pulse in an idle (unqualified) gap: the trigger
    cell is the first event stored after it."""
    rng = random.Random(0x9551)
    await _start(dut)
    pretrig, posttrig = 6, 5
    ring, snap, _ = await _sparse_capture(
        dut, rng, pretrig=pretrig, posttrig=posttrig, trig_event=None,
        arm_after=2, ext_trigger_gap=15, events=40)
    assert (snap["status"] >> 2) & 1, "external-trigger capture never completed"
    window = ring.window(snap["start_ptr"], pretrig + posttrig + 1)
    want = [_event_word(e) for e in range(16 - pretrig, 16 + posttrig + 1)]
    assert window == want, (
        f"got {[hex(w) if w is not None else None for w in window]} "
        f"want {[hex(w) for w in want]}")


@cocotb.test()
@requires("REA-REQ-954")
async def test_rea_req_954_decimation_keeps_every_nth_qualified_sample(dut):
    """DECIM=1 with qualification: every 2nd QUALIFIED sample is stored (the
    decimation counter advances only on qualifying cycles)."""
    rng = random.Random(0x9541)
    await _start(dut)
    # Trigger on event 30 — whether 30 is a kept sample depends on phase, so
    # trigger on a never-matching condition and read the free-running ring.
    ring, snap, _ = await _sparse_capture(
        dut, rng, pretrig=4, posttrig=4, trig_event=None, arm_after=1,
        decim=1, events=30)
    # Event 1 went in before the arm (qualification is arm-time, so it was
    # off then); judge only what the qualified, decimated capture stored.
    stored = [v for v in ring.mem.values() if v & 1]
    numbers = sorted(v >> 1 for v in stored if (v >> 1) > 1)
    assert len(numbers) >= 10, f"too few qualified stores: {numbers}"
    diffs = {b - a for a, b in zip(numbers, numbers[1:])}
    assert diffs == {2}, (
        f"with DECIM=1 consecutive stored events must be 2 apart, got {numbers}")


if __name__ == "__main__":
    main()
