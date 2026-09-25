# `rr_rea` — RouteRTL Embedded Analyzer (REA), v0.7 Spec

## What it is
Architected vendor-neutral on-chip logic analyzer IP, JTAG-attached. Ships **both** vendor JTAG wrappers: Xilinx 7-series (`rr_rea_jtag_xilinx7`, BSCANE2-based) and Intel/Altera (`rr_rea_jtag_intel`, `sld_virtual_jtag`-based) — selected per-vendor by `ip.yml` `synthesis.sources_per_vendor`. Agilex-family parts auto-route the host transport to QuartusStpJtagd (RTL-P3.747); Arria 10 / Stratix 10 / Cyclone V use the openocd vjtag transport. **Frozen 49-bit DR JTAG register interface** at the on-chip layer; on the host side, routertl ships its own first-party client (`REAClient`) that uses the vendored transport for JTAG plumbing only. The seam is clear: routertl owns the capture protocol + register map; the vendored transport owns the JTAG transport layer.

## Why first-party
- **Sliding-window from day one**: the dpram records continuously from reset deassertion. The naive approach gates the dpram write on `armed`, leaving uninit BRAM cells when the trigger fires before `pretrig_len` cycles have elapsed. We don't ship that bug.
- routertl-owned IP in the registry (`rr pkg add routertl/rea`).
- VHDL throughout, modular, contract-first per ROUTERTL conventions.

## Non-goals (v0.1)
- Trigger sequencer (multi-stage)
- Decimation
- External trigger input
- Segmented capture
- Storage qualification
- Edge-detect mode
- Multi-channel mux

These are parked on the version roadmap below.

---

## SW Interface Contract (frozen)

JTAG register map at the burst slave (32-bit words). v0.1 implements the registers below; everything else reads as 0.

| Offset | R/W | Name        | Notes |
|-------:|:---:|:------------|:------|
| `0x00` | RO  | VERSION     | Magic `0x52454109` ('REA' + v0.9 tier: trust tier + PRETRIG_VALID; minor tracks features so the host refuses, not silently degrades). Tier byte is ODD by permanent contract. |
| `0x04` | WO  | CTRL        | bit[0]=arm_toggle, bit[1]=reset_toggle |
| `0x08` | RO  | STATUS      | bit[0]=armed, [1]=triggered, [2]=done, [3]=overflow, [4]=crc_valid, [5]=selftest_busy, [6]=selftest_mode, [7]=selftest_refused |
| `0x0C` | RO  | SAMPLE_W    | Synth-time generic |
| `0x10` | RO  | DEPTH       | Synth-time generic |
| `0x14` | RW  | PRETRIG     | Pretrigger sample count |
| `0x18` | RW  | POSTTRIG    | Posttrigger sample count |
| `0x1C` | RO  | CAPTURE_LEN | = PRETRIG + POSTTRIG + 1 (after `done`) |
| `0x20` | RW  | TRIG_MODE   | bit[0]=value_match; [1]=seq_en, [2]=array_en, [3]=ext_en, [7:4]=op, [8]=ext_and |
| `0x24` | RW  | TRIG_VALUE  | Comparator value — 32-bit window into word `TRIG_WORD_SEL` |
| `0x28` | RW  | TRIG_MASK   | Comparator bitmask — 32-bit window into word `TRIG_WORD_SEL` |
| `0x2C` | RW  | TRIG_WORD_SEL | Bank index for wide TRIG_VALUE/MASK (`G_SAMPLE_W>32`); resets to 0 (RTL-P2.658) |
| `0x30` | RW  | COND_SEL    | Comparator-array slot select (paged); resets to 0 (RTL-P3.647) |
| `0x34` | RW  | COND_CFG    | Slot `COND_SEL`: `{valid[31],lsb_hi[30:28],op[27:24],width[23:16],lsb_lo[15:8]}`; `field_lsb = (lsb_hi<<8)\|lsb_lo` is 11-bit (0..2047, RTL-P2.876/P3.647) |
| `0x38` | RW  | COND_VAL    | Slot `COND_SEL`: 32-bit compare value, right-aligned in the field (RTL-P3.647) |
| `0x3C` | RW  | SOURCE      | Write-side control bits INTO the design; low `G_NUM_SOURCE` bits → `source_out` (sample_clk). Resets to 0 = gated/safe (RTL-P2.837) |
| `0xA0` | RW  | CHAN_SEL    | Must be 0 in v0.1 |
| `0xA4` | RO  | NUM_CHAN    | = 1 in v0.1 |
| `0xB0` | RW  | DECIM       | Decimation ratio — store every (N+1)th sample (24-bit, v0.3) |
| `0xC4` | RO  | TIMESTAMP_W | Exact `G_TIMESTAMP_W`; zero means no timestamp plane |
| `0xC8` | RO  | START_PTR   | Address of oldest sample after `done` |
| `0xCC` | RW  | DATA_WORD_SEL | Bank index for wide captured samples; resets to 0 (RTL-P1.91) |
| `0xD0` | RO  | FEATURES    | Generic-derived config fingerprint: `[7:0]`=G_TRIG_CONDS, `[15:8]`=G_NUM_SOURCE, `[16]`=wide-sample, `[17]`=wide-cond, `[18]`=timestamp plane (`G_TIMESTAMP_W>0`), `[19]`=readback integrity, `[20]`=`axi_stream_window` dump engine elaborated (reserved 0 until REA-P2.5), `[21]`=`udp_window` (Icebox, reserved 0); `[31:22]` reserved 0 |
| `0xD4` | RO  | BUILD_ID    | 32-bit source/content hash (`C_REA_BUILD_ID`, build-generated pkg); 0 = not injected by the build flow (RTL-P3.1198/T2.119) |
| `0xD8` | RW  | DATA_PLANE_SEL | Capture read plane: 0=sample, 1=timestamp; resets to 0 |
| `0xDC` | RW  | SELFTEST_CTRL | bit[0]=fill_toggle; inverse writes request a readback selftest fill |
| `0xE0` | RW  | SELFTEST_SEED | LFSR seed sampled at fill acceptance; 0 selects `0x52454108` |
| `0xE4` | RO  | CRC_SAMPLE  | CRC-32 of the canonical sample-plane page stream; valid when STATUS[4] |
| `0xE8` | RO  | CRC_TS      | Independent CRC-32 of the timestamp-plane page stream; 0 without timestamps |
| `0xEC` | RO  | CAPTURE_EPOCH | Capture generation counter used as the anti-tear anchor |
| `0x0040` | —  | SEQ_BASE    | Reserved window (constant minted in `rea_regbank.yml`; no decode yet — sequencer slots planned) |
| `0x100`+ | RO | DATA_BASE  | DEPTH addresses; each returns word `DATA_WORD_SEL` from `DATA_PLANE_SEL` |

`VERSION` is the exact 32-bit protocol magic `0x52454109`. `CAPTURE_LEN`
updates directly from the configured registers as `PRETRIG + POSTTRIG + 1`
using 32-bit unsigned arithmetic; the host may read it before arm or done.
`TIMESTAMP_W` reports the exact synth-time generic. In v0.7 a nonzero value is
paired with `FEATURES[18]=1` and exposes the timestamp plane below. A v0.6 core
may report nonzero metadata without a plane; the host therefore reads timestamps
only from v0.7 or newer.

### Timestamp capture plane (RTL-T2.123)

When `G_TIMESTAMP_W > 0`, a modulo-`2**G_TIMESTAMP_W` free-running counter
increments on every `sample_clk`. The current counter value and sample are
written to their respective DPRAM planes using the exact same write-enable and
address, preserving one-to-one alignment across decimation gaps, trigger, and
ring wrap. `sample_rst` resets the counter; arm and the CTRL soft-reset pulse do
not, preserving time continuity between capture sessions.

Write 1 to `DATA_PLANE_SEL` and page timestamp words through `DATA_WORD_SEL` and
the existing `DATA_BASE` window. Write 0 to select samples. The host rotates both
planes by the same `START_PTR`, trims both to `CAPTURE_LEN`, and restores both
selectors to 0. `G_TIMESTAMP_W=0` elaborates no timestamp DPRAM, clears
`FEATURES[18]`, and plane 1 reads zero.

### Wide trigger value/mask (`G_SAMPLE_W > 32`, RTL-P2.658)

The comparator covers the full `SAMPLE_W`, but the JTAG datapath is 32-bit, so
`TRIG_VALUE`/`TRIG_MASK` are banked into `⌈SAMPLE_W/32⌉` 32-bit words. To program
word *k*, write *k* to `TRIG_WORD_SEL` (0x2C) then write/read `TRIG_VALUE`/
`TRIG_MASK` — those addresses are a 32-bit window onto word *k*. `TRIG_WORD_SEL`
resets to 0, so a `SAMPLE_W≤32` core — or any host that never touches it — sees
the exact legacy single-word behaviour. The fail-fast elaboration ceiling is
`C_MAX_SAMPLE_W` = **1024 bits** (32 words) as of RTL-P2.876 (was 256); the
banked value/mask storage sizes to `⌈SAMPLE_W/32⌉` for the **instantiated**
width, not the ceiling. `TRIG_WORD_SEL` is 8-bit (256 words of headroom). The
host (`REAClient.configure`) pages trigger values automatically.

### Wide sample-width ceiling + un-ignorable guard (RTL-P2.876/P2.895)

`C_MAX_SAMPLE_W` is **1024**. A probe up to the ceiling (e.g. the field's
704-bit build) elaborates; the trigger value/mask page across `⌈SAMPLE_W/32⌉`
words (22 for 704) and capture readback pages `⌈SAMPLE_W/32⌉` words per cell.

**Wide datapath/config registers are validity-gated, not asynchronously
reset (RTL-P2.897).** `probe_pipe_r`, comparator values/masks, sequencer
values/masks, and the previous-sample register carry no observable state until
their resettable valid/enable controls are asserted. They therefore remain off
the `sample_rst` tree and load on normal sample clocks or atomically on arm.
This prevents reset-recovery fanout from scaling with `G_SAMPLE_W`; FSM state,
pipeline-valid bits, enables, pointers, and counters retain async assertion.

**Over-ceiling is a HARD elaboration error (RTL-P2.895).** A `G_SAMPLE_W >
C_MAX_SAMPLE_W` instantiation fails elaboration in **every** tool via a static
range violation (`constant C_CEILING_GUARD : natural range 0 to 0 :=
boolean'pos(G_SAMPLE_W > C_MAX_SAMPLE_W)` in `rr_rea_capture_fsm`). This exists
because the field shipped 704-bit silicon out of the old 256-bit contract: the
readable `assert … severity failure` is downgraded to a *warning* by vendor
synthesis (Quartus/Vivado), which then produced undefined silicon — the field
signature being that any register read whose value had bit0=1 returned
`0xFFFFFFFF`. The static-range bomb cannot be downgraded to a warning: the build
**halts**. (A `std_logic_vector(0 to N*2-2)` "negative range" bomb does **not**
work — `0 to -2` is a legal NULL range; the subtype-constraint form genuinely
fails.) The friendly assert stays for a readable message. Quartus/Vivado
enforcement is the on-hardware integrator-validation checkpoint (nvc is proven).

### Wide comparator conditions — `field_lsb ≥ 256` (RTL-P2.876)

The per-condition comparator field (`COND_CFG`) previously carried an 8-bit
`field_lsb`, reaching only bits 0..255. RTL-P2.876 extends it to **11 bits** by
adding `lsb_hi` in `COND_CFG[30:28]` (the previously-reserved gap):
`field_lsb = (lsb_hi << 8) | lsb_lo`, range 0..2047, so a condition field can sit
anywhere in a probe up to the ceiling. `field_width` stays 8-bit (a field is
≤255 bits; a wider full-width `==` uses the single-comparator `TRIG_VALUE`/`MASK`
path, RTL-P2.658b). `COND_CFG[7:0]` remains reserved (headroom for RTL-P2.881's
comparator redesign — this encoding does not consume it).

A core advertises the extended decode via `FEATURES[17]=wide_cond`, set exactly
when `G_SAMPLE_W > 256`. `REAClient` writes `lsb_hi` only when the core advertises
`wide_cond` and **refuses** a `field_lsb ≥ 256` on a core that doesn't (it would
decode only the low 8 bits and silently compare the WRONG bits) — plus it rejects
an un-encodable `field_width > 255` or `field_lsb > 2047`. A `field_lsb < 256`
leaves `lsb_hi = 0`, byte-identical to the legacy encoding (back-compat).

### Wide capture readback (`G_SAMPLE_W > 32`, RTL-P1.91)

Capture-data paging is independent of trigger-value paging. `DATA_WORD_SEL`
(0xCC) selects a 32-bit word, while `DATA_BASE + 4*i` continues to address
capture cell *i*. Word 0 contains bits [31:0], word 1 bits [63:32], and so on;
the final partial word is zero-padded. An out-of-range selector reads zero.
`DATA_WORD_SEL` resets to 0, preserving the historical low-word-per-cell view
for an older host and making `SAMPLE_W<=32` byte-identical to v0.4.

For a wide capture, `REAClient` writes each selector in ascending order, reads
the full cell window, merges words little-endian, masks to `SAMPLE_W`, and
restores the selector to 0. It refuses wide capture from a core older than v0.5
instead of silently repeating or truncating word 0.

**Sequencer fields (forward contract, RTL-P3.691).** The multi-stage sequencer's
per-stage `value_a`/`mask_a` are carried full-width (`SAMPLE_W` bits/stage) inside
`rr_rea_capture_fsm` already, but their JTAG register slots (`SEQ_BASE`+) are not
implemented yet. When they are, they **must** follow the same banking rule — a
32-bit window per field paged by `TRIG_WORD_SEL` (or a SEQ-local equivalent),
with `SEQ_STRIDE` unchanged — never a single 32-bit `value_a`/`mask_a`, which
would reintroduce the cap P2.658b removed. See the WIDTH CONTRACT note in
`rr_rea_pkg.vhd`.

### Identity / content fingerprint (`FEATURES` 0xD0, `BUILD_ID` 0xD4, RTL-P3.1198)

`VERSION` (0x00) is a **hand-set magic** (`0x52454109` at the v0.9 tier). Its minor
byte is bumped by hand when the feature tier changes, so a diverged fork — even one
that dropped a fix or rewrote the capture FSM — copies the magic verbatim and reports
as canonical.
The magic cannot identify what is actually on silicon. Two RO registers close that
gap along the two axes a fork can diverge on:

- **`FEATURES` (0xD0)** is a **generic-derived** configuration fingerprint. Every
  field is a function of a synth-time generic (`[7:0]`=`G_TRIG_CONDS`,
  `[15:8]`=`G_NUM_SOURCE`, `[16]`=wide-sample when `G_SAMPLE_W > 32`), so a build
  compiled with a different configuration genuinely reports a different value — it
  cannot be forged by copying a constant. A host validates it against the
  configuration it expects (e.g. "I need 4 comparator slots; chip reports 4").
  This catches **configuration drift**.

- **`BUILD_ID` (0xD4)** is a 32-bit **source/content hash**. It catches **source
  drift** — the exact scenario that motivated the ticket (a ~600-line FSM fork
  reporting an identical `VERSION`) — by hashing the IP source so a fork with
  identical generics but a diverged implementation reports a different id.

  `rr_rea_regbank` reads `C_REA_BUILD_ID` from `rr_rea_build_id_pkg` **directly**
  (RTL-T2.119). The package is a **build-flow-GENERATED input — the IP ships no
  committed copy** (an earlier committed stub collided, same package name, with the
  consumer's generated copy; and a `std_logic_vector` generic didn't survive Vivado
  synthesis). A consuming project **regenerates** the package with the lower 32 bits
  of a SHA-256 over the REA source tree via the `rr_rea_build_id` **pre_build hook**
  (`sdk/scripts/rr_rea_build_id.py`) and lists its own generated copy — so the build
  only ever sees **one** `rr_rea_build_id_pkg`. Wire it in a consuming project:

  ```yaml
  sources:
    syn:
      - ../../ip/routertl/rea/rtl/rr_rea_pkg.vhd
      - generated/rr_rea_build_id_pkg.vhd      # generated per build (git-ignored)
      - ...                                    # (rest of the REA sources + your top)
  hooks:
    pre_build:
      - name: rr_rea_build_id
        script: routertl:sdk/scripts/rr_rea_build_id.py
        args: ["--ip-yml", "../../ip/routertl/rea/ip.yml",
               "--out", "generated/rr_rea_build_id_pkg.vhd"]
        watch: ["../../ip/routertl/rea/rtl/*.vhd"]
  ```

  Standalone IP sim/lint compile the 0-valued fixture
  `sim/cocotb/tests/rea/fixtures/rr_rea_build_id_stub.vhd`.

  `rr synth run` runs the hook first (git-ignore the `generated/` output). See
  `examples/zybo_rea_demo/` for the wired example. A host treats `0` as "unknown
  provenance" (no worse than the old magic-only trust); a non-zero value it compares
  against the released build's known id. **On-silicon** readback of a non-zero
  `BUILD_ID` is the field-validation checkpoint (the register + generic + hook are
  proven in sim: `test_rea_build_id_p3_1203.py` reads an injected id back over JTAG).

### Readback integrity (v0.8, REA-P2.1..3 — in progress)

The v0.8 trust tier adds an automatic CRC-32 sweep over the physical sample and
timestamp page streams after capture, serialized in the exact canonical order and
little-endian byte format read through `DATA_BASE`. `CRC_SAMPLE` and `CRC_TS` publish
atomically; `STATUS[4]` marks them valid. `CAPTURE_EPOCH` brackets host readback so
the pages and CRCs cannot silently come from different capture generations.
The selftest contract fills the sample plane with a deterministic seeded LFSR
pattern, then exercises the same sweep and production readback window; busy, mode,
and refused-command state is reported in `STATUS[5..7]`. `FEATURES[19]` may assert
only when the sweep/selftest logic is elaborated. The tier is COMPLETE: VERSION
reads `0x52454109` — tier byte stays ODD by permanent contract (REA-REQ-806).
[`docs/FDD_REA_TRUST_TIER_V08.md`](docs/FDD_REA_TRUST_TIER_V08.md) is the
authoritative design document.

### Mixed-op AND comparator array (`TRIG_MODE` bit[2], RTL-P3.647)

The single comparator carries one op in `TRIG_MODE[7:4]`, so it expresses one
condition (`==`/`!=`/`<`/`>`/edge) or an all-`==` fold. For a mixed-op AND —
e.g. `counter < 5 AND fsm_state == 1`, or two edges — set `array_enable`
(`TRIG_MODE` bit[2]) and program up to `G_TRIG_CONDS` slots: for each slot,
write `COND_SEL=k` then `COND_CFG` (`{valid,op,width,field_lsb}`) and `COND_VAL`
(right-aligned compare value). The FSM applies each slot's op to its masked
field `probe[field_lsb +: width]` and **ANDs all valid slots**; invalid slots
don't block, and an all-invalid array never fires. Per-condition compare values
are ≤32 bits (a full-width `==` uses the single-comparator path). Inert when
`array_enable=0`; `seq_enable` (bit[1]) takes precedence. `REAClient.configure`
programs the slots from `REAConfig.conditions`; `rr ila` composes them from
signal-named trigger conditions.

### External board-pin trigger (`TRIG_MODE` bit[3]/bit[8], RTL-P3.266)

A package-pin input `ext_trigger_in` (exposed on `rr_rea_jtag_xilinx7`, synced
into the sample-clock domain in `rr_rea_top`) lets a **board pin** participate
in the fire decision — an oscilloscope trigger-out, another FPGA's
`trigger_out` (cross-board sync), or a button. The user routes the pin in their
top + XDC. Enable with `TRIG_MODE` bit[3] (`ext_en`); bit[8] (`ext_and`)
selects the combine:

| `ext_en` | `ext_and` | Fire condition |
|---|---|---|
| 0 | – | internal trigger only (pin ignored — back-compat) |
| 1 | 0 (OR)  | `internal_hit OR ext_pin` — fire on either |
| 1 | 1 (AND) | `internal_hit AND ext_pin` — fire only when both (AND with the auto-trigger internal = "external pin only") |

`trigger_out` (the trig_xbar drive) pulses only on the **true** local fire, so
in AND mode a comparator-only match (pin low) does not ping-pong coupled cores.
Distinct from the trig_xbar `trigger_in` CDC pulse (REA-REQ-400/401), which is
an on-chip cross-core sync and stays an independent OR. A level / wide-pulse
external trigger is synchronized with a double-flop; sub-sample-period pulses
are not guaranteed to be seen. Verified on nvc by REA-REQ-410..413; the
board-pin route + a real scope/cross-board fire is integrator-validated.
`rr ila` sets the bits from `trigger.external: { mode: or|and, signal: <pin> }`.

### Write-side source (`SOURCE` 0x3C, RTL-P2.837)

The read-side path (probes → capture) has a write-side counterpart: an
**ISSP-style source** that lets the host drive control bit(s) **into** the
design from JTAG (System Console / xsdb) — mirroring Intel ISSP "source"
semantics vs the read-only "probe". The motivating case: a downstream BIST FSM
that auto-fires the instant `cal_success` asserts, finishing in low
milliseconds — far faster than arming REA over System Console (~10 s). Gate the
FSM's start on a source bit and the race is gone: program, arm REA at leisure,
then assert the source bit to release.

- **Register.** `SOURCE` (0x3C) is a plain RW register. Its low `G_NUM_SOURCE`
  bits are exposed on the wrapper's `source_out` port; upper bits round-trip on
  readback but never drive the port. Declare the bits in the debug-core yml:

  ```yaml
  sources:
    - { signal: bist_enable, width: 1 }   # bit 0 of SOURCE
  ```

  List order fixes each signal's bit offset (LSB-first by index), exactly like
  `probes` pack the sample word. The declared source-bit total must equal the
  synthesised `G_NUM_SOURCE`.

- **Safe default (the whole point).** `SOURCE` resets to 0 and the synchronizer
  flops init to 0 (GSR), so `source_out` powers up **all-zeros** and stays
  gated across config loads, arm, and reset — **nothing but an explicit
  `SOURCE` write releases it** (no auto-release). Wire it so a bit = 0 holds the
  DUT signal in its safe state, e.g. `bist_start <= bist_start_i and
  source_out(0)`; the reset default then gates by construction. Pinned by
  REA-REQ-700.

- **CDC (non-negotiable — reuse, don't reinvent).** Each source bit crosses
  `jtag_clk → sample_clk` through **`rr_rea_sync_word`** — the proven two-flop
  ASYNC_REG word synchronizer (REA-REQ-020/021) that already carries every REA
  config word (`trig_value`, `trig_mask`, `cond_*`, …). A **single flop is not
  sufficient** regardless of how slowly the JTAG side toggles: fan-out to
  multiple downstream gates can latch inconsistent values before a metastable
  flop resolves. The instance is `u_cdc_source` in `rr_rea_top.vhd`. Verified on
  nvc by REA-REQ-701 (release/re-gate lands in the sample_clk domain) and
  REA-REQ-702 (upper-bit isolation).

- **SDC (integrator MUST apply).** The `source_out` crossing is asynchronous
  and needs the **same `set_clock_groups -asynchronous`** between the JTAG
  (`tck`) and sample clocks as REA's existing config crossings, wherever the
  core is integrated into a board/example design. On Xilinx the BSCANE2 `TCK`
  and on Altera the `sld_virtual_jtag` `tck` are vendor-managed clocks — group
  them asynchronous to the sample clock so the double-flop path is not
  timed/over-constrained. Leaving it implicit is an **unconstrained-but-reported-
  met STA trap**; declare it. `rr_rea_top.vhd`'s `source_out` port comment and
  the debug-core-yml `sources:` schema doc both restate this so a downstream
  integrator can't miss it.

---

## Module hierarchy

```
rr_rea_top                       ← integration
├── rr_rea_jtag_xilinx7          ← BSCANE2 wrapper (HARD MACRO; mocked in sim)
├── rr_rea_jtag_iface            ← BSCAN → reg/burst protocol decoder
├── rr_rea_regbank               ← register map (jtag_clk domain)
├── rr_rea_cdc                   ← jtag_clk ↔ sample_clk syncs
├── rr_rea_capture_fsm           ← trigger detect + sliding-window pointer math
└── rr_rea_dpram (sample buffer) ← BRAM-inferred dual-port
```

Each block has its own VHDL file, package-of-types, and cocotb testbench. None depend on a vendor primitive except `rr_rea_jtag_xilinx7`, which has a behavioral `_sim.vhd` mock for testbenches.

### Multiple REA cores — JTAG USER-chain selection (RTL-P3.642)

`rr_rea_jtag_xilinx7` instantiates **one** BSCANE2 on its `G_CTRL_CHAIN`
generic (`1`=USER1 default, `2`=USER2, `3`=USER3, `4`=USER4 → BSCANE2
`JTAG_CHAIN`). The whole core — control *and* data — lives on that one
USER chain. A design that needs **two** REA cores (e.g. one on a
`clk_sys` domain, one on `clk_ddr`) instantiates each with a **distinct**
`G_CTRL_CHAIN` (USER1 + USER2).

Host side: declare the matching chain in the debug-core yml with
`jtag_chain:` (canonical; alias `ctrl_chain:`, default `1`). `rr ila
capture --core <name>` calls `transport.select_jtag_chain()` before
arm/read so **every** JTAG access (control writes, status polls, and the
data-window block read) targets that core's USER IR — from the
part-specific table (7-series USER1=`0x02`/USER2=`0x03`; UltraScale
USER1=`0x24`/USER2=`0x25`). Before RTL-P3.642 the data-window read was
hard-pinned to USER1, so a second core on USER2+ was uncapturable.

> The wide USER1-control + USER2-burst read optimization (`read_block`
> fast path) is **chain-1-only** by construction — a core whose control
> chain is USER2 falls back to the (now chain-aware) single-frame block
> read. End-to-end two-core capture is verified on-silicon via the
> integrator runbook; the host code-path is unit-gated in
> `sdk/cli/tests/test_rea_chain_select_p3642.py`.

---

## Capture FSM contract (the core fix)

- Trigger latency is internally derived as
  `ceil(G_SAMPLE_W/8) + ceil_log2(G_TRIG_CONDS)`. Each width stage compares one
  8-bit slice into a registered `{eq,gt,lt}` token; condition-array fan-in uses
  registered binary AND levels. There is no user-selected pipeline depth.
- Every local result carries the matching sample's write pointer through the
  derived stages, so `trig_ptr` and DPRAM readback remain sample-relative. The
  on-chip crossbar `trigger_in` pulse remains immediate and still does not drive
  `trigger_out`.
- `wr_ptr` increments every `sample_clk` cycle while `!done`. **Free-running from reset.** Not gated by `armed`.
- `arm_pulse` sets `armed <= 1`, clears `triggered/done`. Does **not** touch `wr_ptr`.
- On the cycle `trigger_hit` fires (and `armed && !triggered`): `trig_ptr <= wr_ptr` AND `triggered <= 1`.
- After trigger: count `posttrig_len` more cycles, then `done <= 1`, `start_ptr <= (trig_ptr - pretrig_len) mod DEPTH`.
- On arm: `overflow` asserts when `pretrig_len + posttrig_len >= DEPTH`; a legal re-arm or reset clears it.
- `dpram_we` is `!done` (always writes when capture is permitted).

This is where we explicitly diverge from the reference ELA design.

---

## Test infrastructure

- Tests in `sim/cocotb/tests/rea/` (repo-root relative; `ip.yml` `build.simulation.test_dir`), runnable via `rr sim run <name>` (ROUTERTL-001 sanctioned engine).
- Each `sim/cocotb/tests/rea/test_*.py` ends with `engine.simulation.run_simulation(...)` per ROUTERTL-001.
- All expected values hard-coded per ROUTERTL-002.
- `requirements.yml` ties every `@requires(REA-REQ-N)` tag to a one-line description; `rr sim coverage-map` enforces the mapping.

---

## Mocking BSCAN

`rr_rea_jtag_xilinx7_sim.vhd` exposes the same port signature as the real Xilinx wrapper but lets the cocotb testbench drive `capture/shift/update/tdi/tdo` directly. Only piece in the hierarchy that can't run untouched in sim — and it's a ~50-line behavioral mock. A future Intel `sld_virtual_jtag` wrapper gets the same `_sim.vhd` treatment.

---

## Migration path

1. Land `rr_rea` v0.1 in `routertl-tool-index/tools/routertl/rea/`. **Done — v0.1 shipped 2026-04-29.**
2. Verify on Zybo against the unmodified reference host SW. **Done.**
3. Switch `examples/zybo_rea_demo/` to instantiate `rr_rea_xilinx7`. **Done.**
4. Sliding-window RTL fix (`mem_we_a = !done && store_enable` + `wr_ptr` not reset on arm) is first-party in `rr_rea`. **Done.**
5. Register `routertl/rea` in the IP registry — first-party debug IP for SDK users. **Done.**

## v0.2 — Host-side ownership (shipped 2026-04-29)

The on-chip RTL is unchanged from v0.1. v0.2 ships:

- **`REAClient` (routertl.sdk.cli.rea)** — first-party SDK host client owning the capture protocol (configure / arm / wait_done / capture). Uses the vendored transport for JTAG plumbing only. Replaces the vendored `Analyzer.capture()` in the `rr ila capture` bridge.
- **Batched dpram readback** — single xsdb `jtag sequence` for all DEPTH cells with `delay 20` between scans (matches the vendored transport's single-reg `READ_IDLE_CYCLES`, which is the timing that works on rr_rea's regbank). One round-trip instead of N. Verified on Zybo Z7-20: capture+read in 1.9 s for DEPTH=4096, down from ~5 s with the v0.1 single-reg fallback.
- **Native `start_ptr`-based rotation** — REAClient reads `ADDR_START_PTR` (0xC8) from the chip and rotates the buffer in software so the trigger sample lands at index `pretrigger` by construction. No timestamp dependency.
- **Synthetic `sample_clk` anchor channel (host-side)** — the bridge appends a 1-bit `sample_clk` channel to the wave_stream_v1 HELO descriptor and emits two sub-samples per real sample so RouteWave displays the clock at the *true* sample frequency (not sample_freq/2). Zero RTL/JTAG cost.

10 unit tests pin REAClient's contract; the existing 34 ila bridge tests carry through with extended fakes for the new transport surface.

---

## External register-bus port (lean profile)

`rr_rea_jtag_iface` is not the analyzer — it is one **bridge** that converts the
JTAG TAP into the plain register bus `rr_rea_regbank` speaks. `G_REG_IFACE`
selects which bridge drives that bus (RTL-P3.931):

| value | bus driven by | TAP ports |
|---|---|---|
| `"jtag"` (default) | `rr_rea_jtag_iface` | live |
| `"external"` | the `reg_*_i` slave ports | **inert**, `tdo_o` tied low |

Exactly ONE bridge exists in a given elaboration. Under `"external"` the TAP
decoder is **not instantiated** — two live masters on one register bus is
silent corruption, so it is not merely idled (REA-REQ-901). Under `"jtag"` the
`reg_*_i` ports have no effect and may be left dangling (REA-REQ-902).

Everything downstream of the bridge — regbank, CDC, capture FSM, comparator
array and the whole v0.8 trust tier — is identical silicon either way
(REA-REQ-900).

**The register bus is a REGISTERED-read bus.** Present the address with
`reg_rd_en_i` and hold it; a regbank register is on `reg_rdata_o` on the
**next** cycle, a `DATA_BASE` capture cell **two** cycles later (BRAM
synchronous read, then the RTL-P1.96 registered paging mux). A master that
samples in the same cycle it presents the address reads the PREVIOUS register;
one that waits a single cycle reads capture cells correctly for a regbank
register and reads the cell addressed *before* the read for the window
(REA-P2.4 found 1.1.0's bridge doing exactly that: the whole window came back
as physical cell 0 over AXI). Wait two cycles for every read — the safe rule
for both — as `rr_rea_axi4lite` now does (REA-REQ-904).

## AXI4-Lite bridge

`rr_rea_axi4lite` is the ready-made AXI4-Lite slave for the external port. It
presents a compliant AXI4-Lite interface and drives the register bus:

- Addressing: byte address, with the low 2 bits ignored (masked to `00` onto
  `reg_addr_o`), so narrow AXI reads (byte and halfword) at `+1..+3` return
  the full register word on the correct byte lanes of `rdata_o` (REA-P2.6).
- One AXI transaction produces exactly ONE register-bus operation. A write
  asserts `reg_wr_en_o` for a single cycle with address and data stable across
  it (REA-REQ-903/907). A double pulse on a TOGGLE register such as `CTRL`
  `arm_toggle` is a silent no-op — it arms and immediately re-arms.
- `aw` and `w` are accepted in **either order or simultaneously**
  (REA-REQ-905); several interconnects present `w` first under backpressure.
- Reads present the address, then wait **two** cycles before asserting
  `rvalid` — the regbank's registered-read cycle plus the `DATA_BASE` window's
  BRAM + paging-mux edge — so `rdata` is the addressed register or capture
  cell and never its predecessor (REA-REQ-904). `rvalid` therefore asserts
  three `aclk` edges after `arvalid` is accepted (was two in 1.1.0, which
  read the DATA window one cell stale — fixed in 1.1.1).
- `bvalid`/`rvalid` are held until their ready is seen and are never asserted
  before the transaction has been applied (REA-REQ-906).
- Responses are always `OKAY`. There is deliberately no decode error: the
  regbank reads unmapped addresses as zero and drops unmapped writes, and a
  debug bus that `SLVERR`s on an unknown offset turns a harmless probe into a
  bus fault.
- `wstrb` is honoured only as all-or-nothing: every rr_rea register is a whole
  32-bit word, several with side effects, so a sub-word write would be a silent
  half-action and is dropped instead (REA-P2.6: `wstrb_i` is latched when `wvalid_i`
  is accepted, so `W_APPLY` evaluates the transaction's own strobes rather than
  floating or subsequent bus state).

## Dump-path transports (REA-P2.4)

[`docs/DUMP_PATH_STRATEGY.md`](docs/DUMP_PATH_STRATEGY.md) is the plan; this
section is the contract it cites (REA-REQ-909..913). **JTAG stays the default
door.** Everything else is a *truck for the window after `STATUS.done`* — not
a second analyser, not a second register map, and never a MAC inside
`rr_rea`. Capture rate is `sample_clk` into BRAM and no transport changes it;
a transport changes only how fast the window leaves the chip.

**The 32-bit register map above is the protocol.** A transport moves either
`addr`/`data` words against that map, or the *window blob* defined below.
It adds no register, no version word and no re-mapping of its own — every
named register (`VERSION`, `STATUS`, `DATA_WORD_SEL`, `DATA_BASE`, …) sits at
the same offset and reads the same value through every door (REA-REQ-909;
`test_rea_dump_path_contract_p2_4` reads both doors on two real cores).
`wave_stream_v1` remains the host → RouteWave seam and is untouched by any of
this.

### Transport names

| Name | Moves | Status | Notes |
|---|---|---|---|
| `jtag` | register map via the TAP (`rr_rea_jtag_iface`) | **shipped** | Default; always sufficient for arm / status / small dump. |
| `axi_lite` | the same register map via `G_REG_IFACE = "external"` + `rr_rea_axi4lite` (mmap / UIO on a PS) | **RTL shipped (1.1.x); host = RTL-P2.1134** | Zynq/HPS: the CPU's existing Ethernet is the "Ethernet flavour". No FPGA MAC. |
| `axi_stream_window` | the window blob as one AXI-Stream burst after `done`; control stays on the register map | **RTL shipped (REA-P2.5)** | `rr_rea_axis_window`, elaborated by `G_AXIS_WINDOW`; advertised by `FEATURES[20]`. Read-only window mover on `m_axis_*`, NOT a second register master. Host = RTL-P2.1135. |
| `udp_window` | the same blob over a proven external MAC, packetiser outside `rr_rea` | **Icebox — REA-ICE.1** | Bare FPGA, no PS. `FEATURES[21]` reserved. |

**v1 = `jtag` + `axi_lite`** (REA-REQ-910). `axi_stream_window` is the
first burst truck and is RTL-adjacent to v1, not in it; `udp_window` is
owner-gated. Two live masters on one register bus remain forbidden — a core
has exactly one door (`G_REG_IFACE`), the TAP is not instantiated under
`"external"` (REA-REQ-901) and `reg_*_i` are inert under `"jtag"`
(REA-REQ-902) — until the dual-door arbiter ticket (REA-P3.4) says otherwise.

### Window blob

The payload every window transport carries, and the reference the burst /
UDP tests must compare against (`sim/cocotb/tests/rea_window_blob.py` is the
executable definition; a green AXIS dump that never compared against
`DATA_BASE` is not done). Frozen by REA-REQ-911/912:

- **Headerless.** All metadata — `SAMPLE_W`, `TIMESTAMP_W`, `DEPTH`,
  `CAPTURE_LEN`, `START_PTR`, `FEATURES` — comes from the register map first;
  the blob is only the cells. Framing (`tlast`, end-of-datagram) is the
  transport's: `tlast` on the last beat of the last plane.
- **`CAPTURE_LEN` cells** per plane, not `DEPTH`.
- **Plane-major:** the whole sample plane, then the whole timestamp plane iff
  `FEATURES[18]` (i.e. `TIMESTAMP_W > 0`).
- **Cell = `ceil(plane_w/32)` little-endian 32-bit words**, word *k* = plane
  bits `[32k+31:32k]`, final partial word zero-padded — byte-for-byte the value
  `DATA_BASE` returns with `DATA_WORD_SEL = k` and `DATA_PLANE_SEL` = plane. One
  paging rule serves both paths.
- **Rotation is engine-side for window transports (decided, REA-REQ-912):**
  blob cell *i* is physical `DATA_BASE` cell `(START_PTR + i) mod DEPTH`, so
  index `PRETRIG` is the trigger sample and a window consumer does **no**
  host-side rotation. Register-map transports (`jtag`, `axi_lite`) are
  unchanged: they walk `DATA_BASE` by physical address and `REAClient` keeps
  rotating on the host from `START_PTR` (the v0.2 contract). The choice is
  tested on both doors: the reference walker builds the engine-ordered blob
  from each and proves the trigger lands at `PRETRIG`, the planes are
  time-monotonic, and the two blobs are byte-identical.

### The AXI-Stream burst engine (REA-REQ-914, REA-P2.5)

`rr_rea_axis_window` is the first window "truck". Elaborated iff
`G_AXIS_WINDOW` (off by default, so a JTAG-only bring-up build carries no burst
engine and `FEATURES[20]` reads 0). It runs in the register-bus clock domain —
`reg_clk_o`, i.e. the PS AXI clock under `G_REG_IFACE = "external"` — and taps
`DATA_BASE` (DPRAM port B) through a **local read-port arbiter**, not the
register bus: it is a read-only window mover, so it is **not** a second
register-bus master and `REA-REQ-901` still stands (the dual-*control*-door
arbiter remains REA-P3.4). After a capture reaches `STATUS.done` the engine
bursts the exact window blob above on `m_axis_*` (one DPRAM read per cell, the
`ceil(plane_w/32)` beats of that cell paged out of a latched cell register),
`tlast` on the last beat of the last plane, full AXI-Stream back-pressure
(`tdata`/`tlast` held while `tvalid` and not `tready`). The burst is
byte-identical to a `DATA_BASE` walk of the same capture, proven in
`test_rea_axis_window_dump_p2_5` against `rea_window_blob.py`.

**`DATA_BASE` is undefined while a dump is in progress (REA-REQ-916).** The
engine and the register-map `DATA_BASE` decode share the *one* DPRAM read port
(port B) through the local arbiter, and while bursting the engine owns it. A
`DATA_BASE` read issued between `STATUS.done` and the burst's `tlast` therefore
returns **whatever cell the engine is walking, not the addressed cell** — no
wait state, no error, silently. This is not a defect the host can walk into
blind: the host **SHALL NOT** walk `DATA_BASE` while a dump is in flight — it
either consumes the window over the `m_axis_*` transport it selected, or waits
for `tlast` (the burst is one-shot per `STATUS.done`) before reading `DATA_BASE`
over the register door. This matters most in the `jtag` door, where `reg_clk_o`
is `TCK` and `m_axis_tready_i` defaults `'1'`: a `G_AXIS_WINDOW` build there
begins dumping at `TCK` as soon as `done` rises and can overlap an `rr ila`
`DATA_BASE` walk. Giving the register decode priority is **not** the fix — it
would steal the engine's own read mid-cell and corrupt the burst instead; the
contract is "one consumer of the window at a time", stated here rather than left
to a code comment. (A `busy`/`dump-in-progress` status bit is a register-map
*semantic* addition and so would force a `VERSION` bump, REA-REQ-806/913;
deferred with the dual-door arbiter, REA-P3.4.)

### Advertising a window transport (REA-REQ-913)

A **`FEATURES` bit, not a `VERSION` bump**, advertises a burst engine:
`FEATURES[20] = 1` iff the `axi_stream_window` dump engine is elaborated
(a synth-time generic in REA-P2.5, derived like every other `FEATURES`
field — never hand-set); `FEATURES[21]` is reserved for `udp_window`. A host
selects a window transport only when its bit reads 1; a core without the
engine reads 0 there today, through both doors. `VERSION` moves only if the
register map itself gains a register or changes a semantic — a transport that
only moves the existing map or the blob does not bump it (the tier byte stays
odd, REA-REQ-806).

### Hard rules (carried from the strategy)

- No MAC, ARP or TCP stack inside `rr_rea`.
- Do not instantiate JTAG and `external` masters together before REA-P3.4.
- Do not change the frozen map "to make Ethernet easier".
- JTAG `rr ila capture` on the existing demos keeps working.

## Lean profile

A **configuration**, not a variant core: `G_SAMPLE_W = 8`, `G_DEPTH = 512`,
`G_TRIG_CONDS = 1`, `G_NUM_CHAN = 1`, `G_NUM_SOURCE = 1`, `G_TIMESTAMP_W = 0`
(REA-REQ-908). No logic is conditionally deleted to make it fit — the sample
buffer is already BRAM-inferred and costs no fabric, so the size lever is the
generic set, dominated by the comparator array.

Measured on `xc7z010clg400-1` (Vivado 2024.1): 890 LUT / 1497 FF / 0.5 BRAM,
against 1497 / 2350 / 5.5 for the default configuration. See
[`docs/LEAN_PROFILE.md`](docs/LEAN_PROFILE.md) for the full table, the
first-AW-beat trigger recipe and an instantiation template.

## Out of scope (parked)

| Version | Feature | Backlog | Status |
|--------:|:--------|:--------|:--|
| v0.2 | Host-side `REAClient` (capture protocol ownership) | RTL-P3.276 | **Shipped** |
| v0.2 | Synthetic `sample_clk` anchor (host-side) | RTL-P3.272 v0.1 promise | **Shipped** |
| v0.2 | Cross-domain trigger crossbar (`rr_rea_trig_xbar`) | RTL-P3.266 + (new) | **Shipped** |
| v0.2 | On-chip sample-clock tick channel (RTL companion to host anchor) | (new) | Parked |
| v0.2 | Edge-detect trigger mode | RTL-P3.263 | Parked |
| v0.3 | Decimation | (new) | **Shipped** |
| v0.3 | Multi-stage trigger sequencer | RTL-P3.265 | **Shipped** |
| v0.5 | Write-side source (ISSP-style `SOURCE`) | RTL-P2.837 | **Shipped** |
| v0.4 | Segmented capture | (new) | Parked |
| v0.4 | Storage qualification | (new) | Parked |
| v0.5 | Multi-channel mux | (new) | Parked |
| v0.5 | Intel JTAG vendor wrapper (`sld_virtual_jtag`) | RTL-P3.427 | **Shipped** |
| v0.6 | Sample-width ceiling 256 → 1024 + un-ignorable over-ceiling guard | RTL-P2.876/P2.895 | **Shipped** |
| v0.6 | Wide comparator conditions (11-bit `field_lsb`, `wide_cond`) | RTL-P2.876 | **Shipped** |
| v0.7 | Aligned per-sample timestamp capture plane | RTL-T2.123 | **Shipped** |
| —    | Serial comparator O(width²) → near-linear storage redesign | RTL-P2.881 | Parked (silicon resource quantification deferred) |

### v0.1 (host-side) — Synthetic clock anchor channel

Each REA instance gets a virtual `clk_<corename>` channel (e.g.
`clk_ila1`, `clk_ila2`) that the routertl `rr ila capture` bridge
synthesizes on the producer side: the channel is added to the
wave_stream_v1 HELO descriptor and emitted as a 1/0 toggle pattern,
one bit per sample. Zero RTL/JTAG cost — every sample is exactly
one cycle apart by construction, so the pattern is honest. Gives
the user a visual anchor on the RouteWave canvas to see the
sample cadence alongside the captured probes.

### v0.2 — Cross-domain trigger crossbar (rr_rea_trig_xbar)

When a design has multiple clock domains (e.g., 125 MHz Ethernet,
250 MHz fabric, 100 MHz processor), the user often wants ONE event
in any domain to freeze the capture in ALL domains, so the captured
windows are time-coherent.

Design sketch:
- Each REA instance exposes a 1-cycle `trigger_out` pulse on its
  own sample_clk when its local trigger fires.
- Each REA instance accepts a `trigger_in` strobe (sync'd to its
  sample_clk via two-flop) — when high, behaves as if its local
  comparator fired.
- A small `rr_rea_trig_xbar` module sits between N instances and
  ORs each domain's `trigger_out` into every other domain's
  `trigger_in`, with the necessary CDC syncs.
- One CTRL.arm bit on the JTAG side fans out to all instances'
  arm_pulse — domains arm together.
- `done` reports per-instance; the host SW waits for all to go high.

This pairs naturally with the wave_stream_v1 nanosecond-based
timestamps already used by the routertl `rr ila capture` bridge —
multi-domain rendering on the consumer side falls out for free
once each window has its own (HELO sample_clk_hz, captured ts)
and a coherent trigger fire moment.

### v0.2 (RTL-side) — On-chip sample-clock tick channel

Optional companion to the host-side anchor: add a 1-bit register
that toggles every `sample_clk` cycle and prepend it to the probe
word inside the REA instance, so the *actual* on-chip sample-clock
state is captured per-sample. Costs +1 bit of `SAMPLE_W` and +1 cell
of dpram per entry. Only meaningful when paired with the cross-domain
trigger crossbar above — that's when an on-chip "this clk really did
tick at this moment" anchor starts to add information beyond the
deterministic host-side toggle.

Requirements catalog will land under REA-REQ-400 series in v0.2.
