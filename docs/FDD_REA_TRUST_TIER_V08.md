# FDD — REA v0.8 "Trust Tier": Readback Integrity (CRC sweep + selftest + host verify)

**Status: DRAFT rev2 for review — not filed, not implemented.**
Facts verified against `rr-rea` @ `b447311` on 2026-07-23.
rev2 (2026-07-23): incorporates the adversarial codex review — epoch protocol,
canonical CRC stream, CDC publication handshake, selftest arbitration,
staged LFSR fill, FEATURES derivation, neutral fault taxonomy.
Companion to `SPEC.md` (v0.7); this FDD defines the v0.8 feature tier.

---

## 1. Problem statement

A REA capture is only as trustworthy as its **JTAG readback path**, and that
path has a proven silicon failure history that RTL simulation and STA both
missed:

- **RTL-P1.96** (Arria 10 / Quartus Pro 26.1): ~20 ps intra-tck HOLD slack
  across the SLD fabric domain corrupted the readback DR. Observed modes:
  *any register value with bit0=1 read as `0xFFFFFFFF`* and *whole-DR
  shifted left by one*. Width-structure-gated (12-bit clean, 704-bit broken),
  seed-robust, immune to an RTL rewrite. Vendor case RTL-P2.901.
- **2026-07 field report** (same bench family): odd capture addresses read
  back incorrectly shifted — the same shift-register-flavoured corruption
  class, currently undiagnosed.

Today's only acceptance gate is the **odd-VERSION probe**: read `VERSION`
(0x00) once and require the deliberately-odd magic `0x52454107`. That
validates exactly **one word** of the readback path. The capture window —
thousands of words, paged through `DATA_WORD_SEL` banking and burst reads —
is unprotected: a shifted or aliased buffer read produces *plausible-looking
wrong waveforms* with no tripwire.

The design goal of v0.8: **no capture is trusted unless the silicon itself
vouches for the bytes the host received.** Two mechanisms, sharing one sweep
engine:

1. **CRC sweep** — after every capture, the core computes a CRC-32 over the
   physical buffer *in the sample-clock domain, directly from BRAM*; the host
   recomputes over the raw pages it read and compares, under an epoch
   protocol that pins both to the same capture generation.
2. **Readback selftest (BIST pattern mode)** — the host fills the sample
   plane with a deterministic on-chip LFSR pattern and reads it back through
   the *identical* `DATA_BASE`/`DATA_WORD_SEL` machinery, validating the full
   readback path word-exactly **before** any real capture is trusted
   (`rr ila selftest`). Generalizes the odd-VERSION probe from 1 word to the
   whole buffer, at every configured width.

Explicitly rejected alternative: a companion soft CPU (RISC-V) reading the
buffer over a fabric bus as an independent witness. Correct in principle,
overreach in cost — the CRC + selftest pair delivers the same trust verdict
for a few hundred LUTs and zero firmware.

### Threat model (what a match does and does not prove)

The CRC/selftest verdict covers the path **BRAM port B → jtag_iface →
vendor JTAG wrapper → cable → host page reads** (including `DATA_WORD_SEL`
banking and burst framing). It does **not** validate the capture side (probe
pipeline, trigger, write pointers) — that is sim-proven and was never the
failing layer. Because the CRC is defined over the exact 32-bit pages the
host reads (§2.4), padding and merge logic are inside the covered surface.

The CRC result registers are themselves read over the same suspect JTAG
path. The failure direction is *usually* safe — a corrupted CRC read
produces a mismatch (distrust) — but this is a **probabilistic claim, not an
impossibility**: a deterministic transform correlated across data and CRC
registers is not automatically a uniform 2^-32 event. Two cheap hardenings
close most of that residue: the host reads each CRC register **twice**
(must agree), and the observed fault modes (shift, bit0-stuck) provably
cannot preserve CRC-32 agreement over a shifted stream. Stated residual
risk is acceptable for a debug-trust tier.

---

## 2. On-chip design

### 2.1 Sweep engine (new logic, `sample_clk` domain)

The dpram is simple dual-port: **port A writes** (`sample_clk`), **port B**
is the JTAG read side. The write enable is `!done` further gated by
`decim_tick` and terminal post-trigger state
(`rr_rea_capture_fsm.vhd:821-834` — the SPEC's `dpram_we = !done` is the
simplified form); the property that matters here is narrower and true:
**after `done`, port A performs no writes**. The sweep engine therefore
muxes onto port A and reads the buffer in the sample-clock domain with zero
contention against concurrent JTAG readback on port B.

Implementation notes a second implementer must not miss:

- Both RAM instances currently tie `dout_a => open`
  (`rr_rea_top.vhd:529-540, 561-572`) — v0.8 **connects both port-A read
  outputs**. Port A reads are synchronous, one-cycle latency: the sweep
  pipeline accounts for the initial fill cycle and one final drain cycle.
- **Port-A ownership arbiter** (single owner at any instant), fixed
  priority: `reset > arm > accepted selftest fill > capture write > sweep`.
  Arm or reset during a sweep aborts it; arm or reset during a fill aborts
  the fill. Every abort bumps `CAPTURE_EPOCH` and clears `crc_valid`
  (§2.3).

Sweep behaviour:

- **Trigger:** automatic on the rising edge of `done` (capture complete) or
  on selftest-fill completion. No host action required.
- **Unit of work:** one 32-bit **page** per cycle — exactly the value the
  `DATA_BASE` window would return for `(plane, cell, DATA_WORD_SEL=k)`,
  including the existing zero-padding of the final partial word (RTL-P1.91
  rules). CRC-32 is stepped 32 bits per cycle (32-bit-parallel XOR
  network).
- **Order:** plane 0 (samples), cells in physical address order
  `0 → DEPTH-1`, pages `k = 0 → ceil(SAMPLE_W/32)-1` within each cell; then,
  when `G_TIMESTAMP_W > 0`, plane 1 (timestamps) with
  `ceil(G_TIMESTAMP_W/32)` pages per cell into an **independent** CRC
  (fresh init — the two plane CRCs never share state).
- **CRC:** CRC-32/IEEE-802.3 (reflected poly `0xEDB88320`, init
  `0xFFFFFFFF`, final XOR `0xFFFFFFFF`, reflected in/out) over the page
  stream serialized **LSByte-first within each 32-bit page** — i.e. the
  host check is `zlib.crc32(pages.astype('<u4').tobytes())` over its raw
  page reads. Full serialization pseudocode in §2.4.
- **Latency:** `DEPTH × ceil(W/32)` cycles per plane (+2 pipeline), planes
  serial, `W` = `SAMPLE_W` or `G_TIMESTAMP_W` respectively. Zybo demo
  (4096 × 32-bit) ≈ 4 k cycles ≈ 33 µs @125 MHz; worst case
  (4096 × 1024-bit) ≈ 131 k cycles ≈ 1 ms — invisible next to the
  seconds-scale host read.
- **Publication (atomic, both planes):** results are **latched** in the
  sample domain only after *both* plane sweeps complete, `CAPTURE_EPOCH`
  is snapshotted alongside, and only then is the publish toggle flipped
  (§2.5). Partial results are never observable; a stale `CRC_TS` can never
  pair with a fresh `CRC_SAMPLE`.

### 2.2 Selftest pattern mode (BIST)

- **Sample plane only.** The timestamp plane is **not** LFSR-filled: its
  `din_a` is hardwired to the free-running counter
  (`rr_rea_top.vhd:542-568`), filling it would need an extra data mux and
  would break the v0.7 "stored timestamp = counter value" semantic. The
  sample-plane word-exact check already exercises address decoding, word
  banking, burst framing, and the physical JTAG path; production timestamp
  reads stay covered by `CRC_TS`.
- **Pattern:** 32-bit maximal-length Fibonacci LFSR, taps 32,22,2,1
  (1-indexed). Precisely: state bits `x[31:0]`,
  `b = x[31] xor x[21] xor x[1] xor x[0]`,
  `x_next = (x >> 1) | (b << 31)`; the **pre-step state is the emitted
  word** (the seed itself is word 0). Seed from `SELFTEST_SEED`, **sampled
  atomically at fill acceptance** in the sample domain (a mid-command seed
  write cannot produce an indeterminate accepted seed). Seed 0 (lockup) is
  substituted with `0x52454108`. Word sequence order: cell-major then
  page-minor (cell 0 pages 0..N-1, cell 1 pages …). Unused high bits of a
  cell's final partial page are zeroed *after* pattern generation, exactly
  as capture padding is — so the expected page stream equals what readback
  must return. Total words per fill (`DEPTH × ceil(SAMPLE_W/32)`, ≤ 2^17 at
  the ceiling) is orders of magnitude below the LFSR period 2^32−1:
  uniqueness of every word is guaranteed, which is what makes address
  aliasing, bank swaps, and shifts all distinguishable.
- **Fill architecture** (resolves the wide-cell contradiction): the LFSR
  steps once per page into a `G_SAMPLE_W`-bit **staging register**; after
  the cell's last page, one full-cell port-A write. Fill latency
  `DEPTH × ceil(SAMPLE_W/32)` cycles. The staging register costs
  `G_SAMPLE_W` FFs — trivial at Zybo demo widths, ~1 k FF at the 1024-bit
  ceiling (reflected in §2.6).
- **Completion:** fill **does not touch capture state** — `done`,
  `triggered`, `START_PTR`, `CAPTURE_LEN` are untouched (and therefore
  describe the *previous* capture; they are meaningless for pattern
  readback, which always reads the full physical window). Completion is
  signalled by `selftest_busy` falling with `selftest_mode` set, after
  which the sweep engine runs and publishes `CRC_SAMPLE` for the pattern.
  `DATA_BASE` reads are plain port-B RAM reads and work regardless of
  `done`. `REAClient.capture()` **refuses** when `selftest_mode=1` — an
  ordinary capture API can never serve LFSR data as a waveform.
- **Arbitration & refusal:** a fill toggle is **refused** (not queued)
  while `armed`, while a fill is already active, or while a capture is
  between arm and done. Refusal sets the sticky `selftest_refused` STATUS
  bit (cleared on the next *accepted* fill, arm, or reset) — so absence of
  activity is distinguishable from CDC delay. An accepted fill bumps
  `CAPTURE_EPOCH`, clears `crc_valid`, and sets `selftest_mode` **before**
  the first pattern write (no window where old-capture CRC still claims a
  half-overwritten buffer). `selftest_mode` clears on arm or reset.

### 2.3 Register map additions (v0.8)

| Offset | R/W | Name | Notes |
|-------:|:---:|:-----|:------|
| `0xDC` | RW | `SELFTEST_CTRL` | bit[0]=fill_toggle. Toggle semantics: host reads current value, writes the inverse; writing the same value is a no-op; toggles while busy/armed are refused (sticky `selftest_refused`). Resets to 0. |
| `0xE0` | RW | `SELFTEST_SEED` | LFSR seed; 0 → internal default `0x52454108`. Sampled at fill acceptance. Resets to 0. |
| `0xE4` | RO | `CRC_SAMPLE` | CRC-32 of plane 0 page stream; valid when `STATUS[4]`. |
| `0xE8` | RO | `CRC_TS` | CRC-32 of plane 1 (independent init); reads 0 when `G_TIMESTAMP_W=0`. |
| `0xEC` | RO | `CAPTURE_EPOCH` | Free-running 32-bit generation counter; increments on every accepted arm, accepted fill, soft reset, and sweep abort. The host's anti-tear anchor (§3.1). |

`STATUS` (0x08) gains: bit[4]=`crc_valid` (both plane CRCs published for
the current epoch), bit[5]=`selftest_busy` (fill or its sweep in flight),
bit[6]=`selftest_mode` (buffer holds LFSR pattern, not a capture),
bit[7]=`selftest_refused` (sticky, see §2.2).

`crc_valid` **clears** on: accepted arm, accepted fill, soft reset, sample
reset, sweep abort — i.e. before any port-A write outside the sweep can
become externally observable.

`FEATURES` (0xD0) gains bit[19]=`readback_integrity`. To preserve the
register's generic-derived-fingerprint invariant (SPEC §identity), bit 19
is **not hand-set**: it is derived from the same package boolean
(`C_HAS_READBACK_INTEGRITY`) whose `generate` elaborates the sweep/selftest
logic — the bit reads 1 exactly when the logic exists in the netlist.
Hosts gate all v0.8 behaviour on this bit, never on VERSION arithmetic.

**VERSION magic:** the tier byte advances to the next **odd** value —
`0x52454109` — because the odd-VERSION acceptance probe depends on bit0=1
(the bit0→all-ones fault mode). Even tier encodings are skipped forever;
wire minor and marketing tier are therefore no longer numerically equal
(v0.8 ↔ `0x09`), and hosts compare **capability bits, not version
ordering** — VERSION stays an exact-magic-family check only.

### 2.4 Contract: canonical page-stream serialization

One definition, shared verbatim by the sweep engine, `REAClient`, and the
REA-REQ goldens (this resolves the "canonical bytes vs transport bytes"
ambiguity — for `SAMPLE_W=33` the stream is **8 bytes per cell**, the two
32-bit pages the host actually reads, not 5 packed payload bytes):

```
for plane in ([0] + ([1] if G_TIMESTAMP_W > 0 else [])):
    crc = 0xFFFFFFFF                      # independent per plane
    W = SAMPLE_W if plane == 0 else G_TIMESTAMP_W
    for cell in range(DEPTH):             # physical order, no rotation
        for k in range(ceil(W / 32)):     # DATA_WORD_SEL order
            page = window_value(plane, cell, k)   # incl. zero-padded tail
            crc = crc32_step(crc, page.to_bytes(4, 'little'))
    CRC[plane] = crc ^ 0xFFFFFFFF
```

Rationale for raw-pre-rotation coverage: the host recompute is a pure
function of the raw pages received — zero dependence on `START_PTR`
rotation or `CAPTURE_LEN` trim logic, which could themselves carry bugs a
window-CRC would mask. Rotation correctness stays covered by existing
REA-REQ sim tests + the selftest's word-exact diff.

### 2.5 CDC — snapshot-and-toggle publication (one new, small structure)

rev2 correction: this **is** a new CDC structure — the existing precedents
are the wrong shape (`u_cdc_source` crosses jtag→sample; the STATUS bits
are independent single-bit syncs, which would permit a torn
valid-before-data race on a 32-bit result). The publication contract:

1. Sweep completes both planes → `CRC_SAMPLE`/`CRC_TS`/epoch snapshot are
   latched in the sample domain and then held stable.
2. A fixed ≥8-sample-clk settle interval elapses.
3. A `publish_toggle` flips, crossing via the standard two-flop.
4. The regbank exposes the latched words and asserts `crc_valid` only
   after observing the toggle — so no CRC bit can still be resolving when
   valid becomes visible.

Invalidation (`crc_valid` clear on arm/fill/reset) crosses the same way in
the opposite sense and always wins races against publication (an epoch
bump after latch but before toggle-observation suppresses the publish).
Config-direction crossings (`SELFTEST_CTRL`/`SEED`) reuse the existing
`rr_rea_sync_word` path unchanged. The integrator
`set_clock_groups -asynchronous` SDC obligation is unchanged.

### 2.6 Resource estimate

| Block | Estimate (7-series) |
|---|---|
| CRC-32 32-bit-parallel XOR network + state | ~150–250 LUT, 70 FF |
| LFSR + fill FSM | ~60 LUT, 50 FF |
| Fill staging register | `G_SAMPLE_W` FF (32 on Zybo demo … 1024 at ceiling) |
| Sweep FSM + port-A arbiter/mux | ~80 LUT, 40 FF |
| Epoch counter + snapshot/toggle CDC | ~40 LUT, ~110 FF |
| **Total (Zybo demo, 32-bit)** | **~400 LUT / ~300 FF, 0 BRAM, 0 DSP** |

To be confirmed by an actual Zybo synth delta (gate: §5, G3).

---

## 3. Host-side design (`REAClient` / `rr ila`)

### 3.1 Capture verify (default-on when advertised)

`REAClient.capture(verify="auto")` — the epoch-bracketed sequence:

1. Poll `STATUS` until `crc_valid`; read `CAPTURE_EPOCH`, `CRC_SAMPLE`
   (+`CRC_TS`) — **each CRC register read twice, must agree** (§1 threat
   model).
2. Read the raw page window (unchanged batched path).
3. Re-read `CAPTURE_EPOCH` + `crc_valid`. Epoch changed or valid dropped →
   outcome `generation-changed` (another agent armed/filled mid-read):
   retry once, then hard error — never classified as corruption.
4. Recompute per §2.4 over the raw pre-rotation pages; compare per plane.
5. Match → proceed exactly as today (rotate, trim, emit), record
   `readback_verified=True` + epoch in the capture metadata.
6. Mismatch → **read-twice arbitration** (zero RTL): re-read the window
   (re-selecting the core's JTAG USER chain first — §3.3), CRC-check
   **both** reads independently, diff them. Outcomes (evidence-based
   labels — the physical cause is attached as a *hypothesis*, never
   asserted):
   - `retry-passed` — first read bad, second verifies: transient.
   - `stable-corruption` — both reads identical and bad: repeatable fault;
     hypotheses: RTL-P1.96-class readback timing, decode defect, stuck
     selector. Points at `rr ila selftest` + the silicon-vs-sim autopsy
     ladder.
   - `unstable-readback` — reads differ: hypotheses: marginal
     cable/signal, concurrent mutation not caught by epoch.
   All non-passing outcomes are hard errors carrying the
   first-divergence signature; never silently return unverified data.
   `verify="off"` is the explicit, logged escape hatch (mirrors
   `RR_NO_LEASE` philosophy).

`verify="auto"` = on when `FEATURES[19]`, off (with one-line notice) on
older cores. `verify="on"` against a core without the feature is a refusal,
not a downgrade.

### 3.2 `rr ila selftest`

New CLI verb (and `rr_ila_selftest` MCP sibling): seed → fill toggle →
poll `selftest_busy` fall (with a **timeout** distinguishing
"command never acknowledged / `selftest_refused` set" from "accepted but
sample clock not progressing" — a stopped sample_clk must not present as a
silent hang) → read the full physical window via the production path →
**word-exact compare** vs the host LFSR reference (the verdict) → CRC
cross-check as a *diagnostic assertion* on the CRC engine itself (a CRC
disagreement with a word-exact-clean read indicts the sweep engine, not
the readback). On word mismatch, classify the divergence signature against
the known-fault catalog:

- `bit0-all-ones` — words with bit0=1 read `0xFFFFFFFF` (RTL-P1.96 mode A)
- `dr-shift-1` — whole-window bit shift (RTL-P1.96 mode B)
- `addr-parity-alias` — odd/even cell addresses swapped or shifted
  (2026-07 field signature)
- `word-bank-swap` — `DATA_WORD_SEL` pages misordered
- `unclassified` — dump first N divergent (addr, page, expected, got)

Intended standing use: bring-up runbook pre-flight — *no capture from a new
SOF is trusted until `rr ila selftest` passes on that SOF* — replacing the
one-word odd-VERSION probe as the acceptance gate (the probe remains as the
fast first-line check).

### 3.3 Selector & chain ownership (host discipline)

`DATA_PLANE_SEL`/`DATA_WORD_SEL` are shared mutable state: every compound
operation (verify read, retry read, selftest read) (a) re-selects the
core's declared JTAG USER chain first (multi-core designs — RTL-P3.642),
(b) holds the per-core operation lock `REAClient` already implies, and
(c) restores both selectors to 0 in a `finally` path. A retry that skips
(a) can read a *different core* and be misclassified as corruption.

### 3.4 Doc/runbook touchpoints

`SPEC.md` register table + STATUS/FEATURES rows + §2.4 serialization,
`rea_regbank.yml` (5 new registers), `rea-issp-jtag-debug` skill core rule
(acceptance-gate upgrade), integrator runbook checkpoint (§5 G4).

---

## 4. Requirements catalog (new, REA-REQ-800 series)

| REQ | Assertion (sim-verifiable on nvc unless noted) |
|---|---|
| REA-REQ-800 | CRC sweep auto-runs on `done`; `crc_valid` sets only after both planes publish |
| REA-REQ-801 | `CRC_SAMPLE` equals hard-coded golden CRC-32 of the §2.4 page stream (ROUTERTL-002) |
| REA-REQ-802 | Sweep does not perturb concurrent port-B JTAG readback (interleaved read during sweep returns correct data) |
| REA-REQ-803 | Arm / soft reset aborts sweep and fill, clears `crc_valid`, bumps `CAPTURE_EPOCH` |
| REA-REQ-804 | Timestamp-plane CRC matches golden with independent init; `G_TIMESTAMP_W=0` → `CRC_TS`=0 |
| REA-REQ-805 | Wide-sample CRC: page stream identical to `DATA_WORD_SEL` window values incl. zero-padded tail (goldens at 33, 64, 704 bits) |
| REA-REQ-806 | `FEATURES[19]` derived from the elaboration constant, not hand-set; VERSION tier byte odd (structural test) |
| REA-REQ-807 | `CAPTURE_EPOCH` increments on accepted arm, accepted fill, soft reset, abort — and on nothing else |
| REA-REQ-808 | Publication ordering: `crc_valid` never observable while a CRC register still carries a pre-latch value (torn-publish test); invalidation beats an in-flight publish |
| REA-REQ-850 | Fill writes the exact §2.2 LFSR sequence (hard-coded golden prefix incl. seed-as-word-0 and partial-page zeroing) |
| REA-REQ-851 | Seed 0 substitution; seed sampled at acceptance (mid-fill seed write has no effect) |
| REA-REQ-852 | Fill refused while armed / while busy → sticky `selftest_refused`; capture state (`done`, `START_PTR`, `CAPTURE_LEN`) untouched by fill |
| REA-REQ-853 | `selftest_mode` set before first pattern write, cleared by arm/reset; readback of the filled buffer via the production path is word-exact |
| REA-REQ-854 | Timestamp plane is NOT written by fill (counter semantics preserved) |
| Host | Unit tests: epoch-bracket verify (incl. `generation-changed` on mid-read arm), double-read CRC registers, mismatch → dual-CRC read-twice outcome taxonomy (fixtures for all four named signatures + `retry-passed`), `verify=on` refusal on old core, selftest timeout split (refused vs clock-stopped), selector/chain restore in `finally` |

Adversarial notes (bug-finding-is-success): REQ-802 and REQ-808 are the
likeliest to surface real defects (port-A mux vs late `done`-edge races;
torn publication). REQ-805's 704-bit golden intentionally sits at the
field's real operating point.

---

## 5. Zybo-first integration plan

Deliberate choice: **first silicon validation on Zybo Z7-20**
(`examples/zybo_rea_demo`, BSCANE2/xsdb path), *not* Arria 10. Rationale:
the Zybo loop is minutes (local bench, `rr program run`, xsdb), the Arria 10
loop is the slow remote-bench path — and the *feature under test is
readback trust itself*, so it must first be proven on a bench whose readback
path is known-good (Zybo has no P1.96-class history) before being used to
*diagnose* the bench that isn't.

| Gate | Deliverable | Exit criterion |
|---|---|---|
| G1 | RTL + REA-REQ-800..854 on nvc | `rr sim run` green; coverage-map maps all new REQs |
| G2 | Host: REAClient verify + `rr ila selftest` + unit tests | unit suite green; classifier fixtures pass |
| G3 | Zybo synth via `rr queue` | timing met; resource delta ≈ §2.6; BUILD_ID injected |
| G4 | Zybo silicon: selftest pass + verified capture + **fault-injection check** (deliberately mis-merge host pages → verify MUST fail with the right outcome label; deliberately re-arm mid-read → MUST classify `generation-changed`) | all observed; guards against a vacuous always-pass verify |
| G5 | Arria 10 field deployment: run `rr ila selftest` on the bench showing the odd-address signature | signature classified; feeds the open investigation (this is the payoff) |

G4's fault-injection step is non-negotiable — a verify gate must be proven
to **fail** on bad data before its pass is worth anything (the
gate-enforcement-not-detection lesson, D.142/H.86).

---

## 6. Out of scope (this tier)

- Companion soft-CPU witness (rejected — §1).
- LFSR fill of the timestamp plane (rejected rev2 — needs a timestamp-data
  mux and breaks the v0.7 counter semantic; `CRC_TS` covers production
  timestamp readback).
- CRC over the JTAG *transport frames* themselves (per-burst CRC): parked;
  the buffer CRC subsumes its trust value for capture data.
- Segmented capture / storage qualification / sequencer JTAG slots /
  P2.881 comparator redesign: independent roadmap items, re-ranked
  separately (storage qualification is the strongest candidate now that
  the timestamp plane exists).
- Hardening the *write*/config direction (a corrupted config write is
  caught today by read-verify of RW registers in `REAClient.configure`).

## 7. Open questions for review

1. ~~read-then-check vs blocking on `crc_valid`~~ — **resolved rev2**: the
   epoch-bracketed sequence (§3.1) makes read-then-check safe; polling
   `crc_valid` first costs nothing next to the seconds-scale read and is
   required anyway.
2. ~~selftest timestamp-plane fill~~ — **resolved rev2**: sample plane
   only (§2.2, §6).
3. Register window: is `0xDC..0xEC` acceptable, or reserve `0xE0+` for a
   future block and pack these lower? (`0xDC..0xFC` is currently free
   between `DATA_PLANE_SEL` 0xD8 and `DATA_BASE` 0x100 — verified against
   `rea_regbank.yml` @ b447311.)
4. Tier-byte-must-be-odd + capability-bits-not-version-ordering as
   permanent contract: any objection?
5. Should `selftest_refused` also latch a 3-bit refusal *reason* (armed /
   busy / mid-capture), or is the single sticky bit enough for the CLI's
   timeout split?
