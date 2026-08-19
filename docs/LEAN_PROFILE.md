<!-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure -->
<!-- SPDX-License-Identifier: MIT -->

# REA lean profile — a capture core for a bus-attached debug build

**RTL-P3.931.** The sanctioned replacement for a hand-rolled on-wire capture
block. If you are about to write one, read this first.

## Why this exists

`rr-idp`'s `gen_dbg_cap` is a hand-rolled twin of `rr_rea`: an on-wire AW/W
capture with an **unsynchronised CDC** (IDP-P2.11). It was written because
`rr_rea` looked like it did not fit — and because REA's readback was JTAG-only,
which a design with a control bus and no spare BSCAN user chain cannot use.

Both objections are now answered, and neither needed a second capture core.

## It does not overflow — the default config does

The size lever is the **generic set**, not the storage. `rr_rea_dpram` is
already BRAM-inferred (`rr_rea_dpram.vhd`, 0 slices) — "BRAM-ify it" is not the
fix, because it is already BRAM. What costs fabric is the **control logic**:
the trigger FSM, the comparator array (`G_TRIG_CONDS` slots, each a full
masked compare across `G_SAMPLE_W`), the CDC and the regbank.

The lean profile:

| Generic | Lean | Default | Why |
|---|---|---|---|
| `G_SAMPLE_W` | 8 | 12 | one byte of probe is enough for a handshake/state trace |
| `G_DEPTH` | 512 | 4096 | one BRAM either way; 512 is a deep enough window for a bus stall |
| `G_TRIG_CONDS` | **1** | 4 | the comparator array is the dominant fabric cost — 4 slots is 4× the compare logic |
| `G_NUM_CHAN` | 1 | 1 | — |
| `G_NUM_SOURCE` | 1 | 1 | — |
| `G_TIMESTAMP_W` | **0** | 32 | drops the whole timestamp plane (counter + second BRAM port path) |
| `G_REG_IFACE` | `"external"` | `"jtag"` | see below |

### Measured, not estimated

Vivado 2024.1 synthesis, `xc7z010clg400-1`, both configurations through the
same wrapper (RTL-P3.931):

| Configuration | LUTs | FFs | BRAM tiles | DSPs |
|---|---|---|---|---|
| Default (`SAMPLE_W=12, DEPTH=4096, TRIG_CONDS=4, TIMESTAMP_W=32`, JTAG) | 1497 | 2350 | 5.5 | 0 |
| **Lean** (`8 / 512 / 1 / 0`, external + AXI4-Lite) | **890** | **1497** | **0.5** | 0 |
| Lean as a fraction | 59 % | 64 % | **9 %** | — |

Two things this measurement corrects, and they matter more than the saving:

- **The default does NOT overflow an xc7z010.** It lands at 8.5 % of LUTs and
  9.2 % of BRAM on the smallest Zynq-7. RTL-P3.931 was filed believing it did.
  If a design ran out of room with REA in it, the overflow was the design plus
  REA, not REA — so *measure your own build* before assuming the analyzer is
  what has to shrink.
- **The lean profile is not ~100–150 slices.** 890 LUTs is roughly 220 slices;
  the original estimate was optimistic by about 2×, and it predates the v0.8
  trust tier (CRC sweep + selftest fill + trust core), which is real logic that
  did not exist when the number was guessed.

The honest summary: the lean profile buys **~40 % of the fabric and 91 % of the
BRAM**, and — the actual reason to use it — a readback path that does not need
JTAG. Re-measure for your own part before relying on any of these numbers.

## Readback without JTAG

`rr_rea_jtag_iface` was never the analyzer — it is one **bridge** that converts
the TAP into a plain register bus. `G_REG_IFACE => "external"` (REA-REQ-900)
leaves that bus exposed on `reg_*_i` / `reg_rdata_o` so anything can drive it,
and `rr_rea_axi4lite` is the ready-made AXI4-Lite bridge.

The capture FSM, comparator array, CDC and the whole v0.8 trust tier are
**identical silicon** either way. You are not getting a cut-down analyzer; you
are getting the same one through a different door.

Two prohibitions worth knowing, because they are what makes this safe:

- With `"external"`, the TAP decoder is **not instantiated** and `tdo_o` is
  tied low (REA-REQ-901). Two live masters on one register bus is silent
  corruption, so the TAP is not merely idled.
- With `"jtag"` (the default), the `reg_*_i` ports have **no effect**
  (REA-REQ-902) — leave them dangling.

### The registered-read trap

`rr_rea_regbank`'s `rd_data_o` is **registered** (the RTL-P1.96 read-path
pipelining). Present the address, read on the **next** cycle. A master that
samples in the same cycle it presents the address gets the **previous**
register — and a single read still looks fine, so only reading two registers
in a row exposes it. This is the "every cell lags by one" signature that has
cost real bring-up time on two vendors. **The `DATA_BASE` capture window is
one edge deeper still**: the BRAM's synchronous read plus the registered
paging mux put a capture cell **two** edges behind the address, so a bridge
that waits only the regbank's one edge reads every cell as the cell addressed
*before* the read began (1.1.0 did exactly that over AXI — the whole window
came back as physical cell 0 — found and fixed under REA-P2.4). Present the
address, hold it, sample **two** cycles later; that is correct for both.
`rr_rea_axi4lite` handles it; a hand-written bridge must too, and REA-REQ-904
is the requirement — proven on the regbank by `test_rea_axi4lite_p3_931` and
on the DATA window by `test_rea_dump_path_contract_p2_4`.

## Triggering on the first AW beat

No new RTL: this is the existing single comparator with a rising-edge op.

1. Wire the beat you care about into a probe bit, e.g.
   `probe_i(0) <= awvalid and awready;` and put whatever else you want to see
   (state, `wlast`, a FIFO level) in the remaining seven bits.
2. Program the trigger over your bus:

   | Register | Value | Meaning |
   |---|---|---|
   | `TRIG_VALUE` (0x24) | `0x01` | match on bit 0 high |
   | `TRIG_MASK` (0x28) | `0x01` | care about bit 0 only |
   | `TRIG_MODE` (0x20) | `0x41` | `bit[0]` value_match + op `RISE` in `[7:4]` |
   | `PRETRIG` (0x14) | e.g. `16` | beats retained BEFORE the first AW |
   | `POSTTRIG` (0x18) | e.g. `480` | beats after |

3. Arm by toggling `CTRL` bit 0, poll `STATUS` bit 2 for done, then read the
   capture window from `DATA_BASE` (0x100).

`RISE` fires on the 0→1 edge, so it catches the **first** beat of a burst
rather than every cycle `awvalid and awready` happens to be high. That
distinction is the whole reason to use the edge op here.

## Instantiating it

```vhdl
u_dbg : entity rr_rea.rr_rea_top
    generic map (
        G_SAMPLE_W => 8, G_DEPTH => 512, G_TRIG_CONDS => 1,
        G_TIMESTAMP_W => 0, G_NUM_CHAN => 1, G_NUM_SOURCE => 1,
        G_REG_IFACE => "external")
    port map (
        sample_clk_i => aclk, sample_rst_i => arst, probe_i => dbg_probe,
        trigger_o => dbg_trigger, source_o => open,
        -- TAP unused in external mode; tie off.
        arst_i => arst, tck_i => '0', tdi_i => '0', tdo_o => open,
        capture_i => '0', shift_en_i => '0', update_i => '0', sel_i => '0',
        reg_clk_i => aclk, reg_rst_i => arst,
        reg_wr_en_i => reg_wr_en, reg_rd_en_i => reg_rd_en,
        reg_addr_i => reg_addr, reg_wdata_i => reg_wdata,
        reg_rdata_o => reg_rdata);
```

Drive `reg_*` from `rr_rea_axi4lite` (see
`sim/cocotb/tests/fixtures/rr_rea_axi_harness.vhd` for the exact wiring) or
from your own bus master.

## What you get that a hand-rolled block does not

A **synchronised** CDC (two-flop word sync + toggle-pulse transfer,
REA-REQ-020/021 — the thing `gen_dbg_cap` got wrong), the v0.8 readback trust
tier (on-chip CRC sweep and LFSR selftest cross-checked against the host), an
epoch-bracketed capture generation, native RouteWave rendering, and a contract
of 93 requirements with a cocotb test behind each one.
