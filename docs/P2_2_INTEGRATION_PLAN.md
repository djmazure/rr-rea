# REA-P2.2 part 2 — crc_sweep integration into rr_rea_top (design note)

**Status: plan — grounds the integration RTL. Facts verified against rr-rea @ HEAD.**
Part 1 (the `rr_rea_crc_sweep` engine + golden unit test) is landed (e85beae).
This note specifies wiring it into `rr_rea_top` + `rr_rea_regbank` to cover
REA-REQ-800/802/803/804/807/808 (+810/811 arbiter). Contract: FDD §2.1/2.5.

## Current top-level facts (rr_rea_top.vhd)

- Sample dpram `u_dpram` and timestamp dpram `u_timestamp_dpram` both drive
  port A from the capture FSM on `sample_clk_i`:
  `we_a_i => dpram_we_sclk`, `addr_a_i => dpram_addr_sclk`,
  `din_a_i => dpram_din_sclk` / `timestamp_r`, and **`dout_a_o => open`**.
- `dpram_we_sclk` is `!done` (gated by decim/terminal) — so after `done`,
  **port A performs no writes**; only its address is still FSM-driven.
- Capture-FSM outputs available: `done_sclk`, `dpram_we_sclk`,
  `dpram_addr_sclk` (width `C_PTR_W = clog2(G_DEPTH)`).
- dpram port A exposes a synchronous read (`dout_a_o` of `addr_a_i`, 1-cycle
  latency) — matches the engine's `mem_dout_i` latency contract.
- CDC primitives already in the file: `rr_rea_sync_word` (2-flop word sync,
  used for every config word) and `rr_rea_pulse_xfer` (toggle→pulse).

## Engine port recap (rr_rea_crc_sweep, landed)

`sample_clk_i, sample_rst_i, start_i, mem_dout_i, mem_addr_o(clog2 DEPTH),
mem_rd_en_o, busy_o, crc_done_o, crc_o(32)`.

## Integration blocks

### 1. Port-A arbiter + read mux (REQ-802, 810)
Port A of each dpram is time-shared: capture **writes** during `!done`, the
sweep **reads** after `done`. A single-owner mux (per dpram):

```
sweep_owns_a <= sweep_busy   -- sweep only ever runs after done (writes are off)
we_a_i   <= '0'            when sweep_owns_a else dpram_we_sclk
addr_a_i <= sweep_mem_addr when sweep_owns_a else dpram_addr_sclk
din_a_i  <= dpram_din_sclk   -- unchanged; irrelevant while we_a_i='0'
dout_a_o => sweep_mem_dout   -- CONNECT (was open)
```

Priority (REQ-810), fixed: `reset > arm > (fill: P2.3) > capture-write > sweep`.
In P2.2 (no fill) this reduces to: a new arm/reset aborts the sweep (drops
`sweep_owns_a`) and capture reclaims port A. The engine's own `sample_rst_i`
+ a start-gating term enforce this — the sweep cannot be busy while
`dpram_we_sclk` is high (they are mutually exclusive by construction, since
`sweep_busy` only asserts after `done` and `dpram_we_sclk` is `!done`). This is
the REQ-810/811 invariant the formal props assert.

### 2. Sweep start + two-plane engines (REQ-800, 804)
- `sweep_start <= done_sclk and not prev_done` (rising edge of done).
- Instantiate `u_crc_sweep_sample` reading `u_dpram` port A.
- `generate` `u_crc_sweep_ts` reading `u_timestamp_dpram` port A **only when
  `G_TIMESTAMP_W > 0`**; else `crc_ts = 0`, its `crc_done` tied '1'.
  (Two parallel engines — simpler + faster than the FDD's serial single sweep;
  the atomic publication in §4 still gates on BOTH `crc_done`.)
- Each engine reads its own plane's full width; goldens per REQ-801/805 already
  proven on the engine in part 1.

### 3. CAPTURE_EPOCH counter (REQ-807)
Sample-domain 32-bit counter `capture_epoch_r`, increments on **exactly**:
accepted arm pulse, soft reset pulse, sample reset, sweep abort (and accepted
fill in P2.3). Nothing else (a completed sweep / JTAG read / refused op does
not bump it). This is the host's anti-tear anchor.

### 4. Publication — snapshot + toggle (REQ-808, the torn-publish guard)
Sample domain:
1. When `sample_crc_done AND ts_crc_done` (both planes) and not already
   published for this generation: latch `crc_sample_r <= crc_o(sample)`,
   `crc_ts_r <= crc_o(ts)`, `epoch_snapshot_r <= capture_epoch_r`.
2. Wait a fixed `≥8` sample-clk settle (`publish_settle_cnt`).
3. Flip `publish_toggle_r`.
An invalidation (arm/reset/abort → epoch bump) between latch and toggle
**suppresses** the publish (compare `epoch_snapshot_r` to `capture_epoch_r`;
mismatch cancels), so `crc_valid` never rises for a stale generation.

Cross to jtag domain:
- `publish_toggle_r` → `rr_rea_pulse_xfer` → sets `crc_valid` (jtag side),
  cleared by an arm/reset pulse crossed the same way.
- `crc_sample_r`, `crc_ts_r`, `capture_epoch_r` → `rr_rea_sync_word` (three
  32-bit crossings). `crc_valid` is only asserted AFTER the toggle is observed,
  so no CRC bit is still resolving when valid becomes visible (REQ-808).

### 5. Regbank decode (rr_rea_regbank.vhd)
New RO readback (constants already minted in P2.1):
- `CRC_SAMPLE` (0xE4) ← `crc_sample_jclk`
- `CRC_TS` (0xE8) ← `crc_ts_jclk`
- `CAPTURE_EPOCH` (0xEC) ← `capture_epoch_jclk`
- `STATUS[4]=crc_valid`, `[5]=selftest_busy(0 in P2.2)`,
  `[6]=selftest_mode(0)`, `[7]=selftest_refused(0)` — bit 4 live now, 5..7
  wired to '0' until P2.3.
- `FEATURES[19]` ← `C_HAS_READBACK_INTEGRITY` (flip to `true` when P2.3
  completes the tier; stays `false` through P2.2 so the host doesn't advertise
  selftest before fill exists — the sim REQ tests drive the DUT directly, not
  via FEATURES gating).
New regbank input ports (jtag domain): `crc_sample_i`, `crc_ts_i`,
`capture_epoch_i`, `crc_valid_i` — all `_i` per the ESA convention.

## Test plan (rr-rea, contract-first)
- `test_rea_crc_integration` (top-level, mocked BSCAN like `test_rea_top`):
  drive a known capture, wait `done`, poll `crc_valid`, read `CRC_SAMPLE` over
  JTAG, assert == the golden CRC of the captured window (hard-coded,
  zlib-derived) — REQ-800/801-integration.
- REQ-802: interleave a `DATA_BASE` port-B read during the sweep; assert
  unchanged values.
- REQ-803/807: arm mid-sweep → `crc_valid` clears, `CAPTURE_EPOCH` increments.
- REQ-808: assert `crc_valid` is never set with a half-updated CRC (drive an
  arm one cycle before the toggle; publish suppressed).
- REQ-804: `G_TIMESTAMP_W>0` build → `CRC_TS` matches golden; `=0` → reads 0.
Formal (REA-P3.1): the arbiter/epoch/publication PSL props on the
forward-contract signals, anti-vacuity gated.

## Build order
1. Epoch counter + STATUS[4..7] + regbank readback of CRC/EPOCH (smallest,
   testable first) — no port-A touch.
2. crc_sweep instance(s) + port-A mux + dout_a wiring + sweep_start.
3. Publication snapshot/toggle CDC + crc_valid.
4. Integration tests (REQ-800/802/803/804/807/808), then flip nothing on
   FEATURES[19] (that waits for P2.3).
