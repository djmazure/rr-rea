# AGENTS.md — rr_rea (RouteRTL Embedded Analyzer)

Repo-specific conventions for `rr-rea`, the first-party embedded logic-analyzer
IP. REA **graduated** from a routertl-vendored IP to a standalone registry IP
(`rr pkg add routertl/rea`) — this repo is its canonical home. It is developed
and **tested standalone**: tests resolve this repo's own `rtl/` (never a
vendored `ip/routertl/rea/` path — that fossil was removed 2026-07-23).

**There is no second tree to sync to any more (RTL-P2.989, 2026-08-05.)** The
SDK's byte-identical builtin under `routertl/ip/routertl/rea/`, `rea_sync.py`
and the byte-for-byte drift gate are all GONE — this repo is the sole source of
shipped REA RTL and the registry is the only distribution path. Anything that
still tells you to mirror a change into routertl is stale.

## What REA can do — capability summary for agents

Read this before planning a capture or an integration. Every line is backed by
`SPEC.md`; where a feature is limited or open, the ticket is named. (Checked
against this repo at `c2a63b3` and the RouteRTL SDK at `a9013711`,
2026-09-25.)

**Use it through the SDK.** `rr pkg add routertl/rea`, declare the instance in
`debug/<core>.yml` (probe list = the `probe_i` concatenation, LSB first), then
`rr ila identity` → `rr ila selftest` → `rr ila capture --core <name>`. Never a
vendor ILA / SignalTap in a RouteRTL project.

| Feature | Enable | Notes |
|---|---|---|
| Single comparator: `==` `!=` `<` `>` rising falling on one masked field, or an all-`==` AND | always | `rr ila capture --trigger 'state == 3'`, `'rising(irq)'` |
| Mixed-op AND of up to `G_TRIG_CONDS` conditions | always | the host picks the array automatically; each condition value ≤ 32 bits |
| External board-pin trigger (OR / AND) | `ext_trigger_i` | only `rr_rea_xilinx7` exposes the port (REA-P3.6) |
| Cross-core trigger | `trigger_o` + `rr_rea_trig_xbar` | freeze several clock domains together |
| Decimation | `DECIM` | `--decim N`: store 1 in N+1; windows count stored cells |
| Storage qualification | `G_QUAL_CONDS` 1..15 **and** `G_TIMESTAMP_W > 0` | store only when EQ/NE/RISE/FALL slots hold (AND/OR); `--qualify 'EXPR'`, `--qualify-any`, or `storage_qualifier:` in the yml |
| Timestamp plane | `G_TIMESTAMP_W` (default 32) | per-sample `sample_clk` count; exports of gapped captures are placed by it |
| `PRETRIG_VALID` | always | the host trims pre-trigger cells older than this capture |
| Write-side SOURCE | `G_NUM_SOURCE` | JTAG-driven bits into the design, reset 0, no auto-release; `rr ila source` |
| Identity | always | `VERSION` magic, generic-derived `FEATURES`, source-hash `BUILD_ID` (build hook) |
| Readback integrity | always | CRC-32 of both planes + capture epoch; `rr ila selftest` |
| AXI4-Lite door | `G_REG_IFACE => "external"` + `rr_rea_axi4lite` | `--transport axi\|mmio`; the TAP is not instantiated |
| AXI-Stream window dump | `G_AXIS_WINDOW => true` | one consumer of the window at a time (REA-REQ-916) |
| Wide probes | `G_SAMPLE_W` up to 1024 | trigger and readback paged in 32-bit words |

**Not available:** a multi-stage trigger sequencer (in the FSM, not wired to
`rr_rea_top` or the host — REA-P3.7), segmented capture, `G_NUM_CHAN > 1`.

**Vendor wrappers.** `rr_rea_xilinx7` (BSCANE2, `G_CTRL_CHAIN` = USERn) and
`rr_rea_intel` (`sld_virtual_jtag`, `G_CTRL_CHAIN` = `sld_instance_index`).
Neither passes `G_TRIG_CONDS` through (always 4; REA-P3.6). The Microchip UJTAG
wrapper currently lives in the RouteRTL tree, not in this package (REA-P2.13).
Connect `trigger_o`: with no observable output the hierarchy can be pruned.

**Integration rules.** Clock REA from the observed boundary's own clock, and
reset it from a power-on reset the observed block cannot gate. On AMD the
package ships `constraints/rr_rea_scoped.xdc` (TCK clock plus
`set_max_delay -datapath_only` at half the faster period on every
synchronizer first stage), so the consumer adds no REA timing constraints. On
Intel the equivalent SDC is not shipped yet (REA-P2.12): constrain it
yourself the same way. Never waive the crossings with
`set_clock_groups -asynchronous`. Write all configuration while disarmed, then
arm.

**Cost and speed (Vivado 2024.1, xc7z020-1, routed OOC, REA-P2.9, 2026-09-25):**
8×1024 with no timestamps and 1 condition, 1027 LUT / 1520 FF / 0.5 BRAM
tile; the defaults (12×4096, ts32, 4 conditions), 1609 / 2465 / 5.5; 80×4096
with ts32, 5358 / 5461 / 13. The sample-clock Fmax of those builds is
136 / 145 / 156 MHz, capped by REA-T2.5 (229 / 214 / 151 MHz before REA-T2.4);
REA-P2.11 is the next limiter.

**Open defects to design around** (check `tlog list --repo rr-rea` for their
current state):

- Window limit (REA-T2.6, fixed in v0.13 / ip 1.5.0): the TRIG_LATENCY
  (0xF4) samples stored while the trigger pipeline catches up land past the
  trigger cell whatever POSTTRIG says, so `PRETRIG + max(POSTTRIG,
  TRIG_LATENCY) >= DEPTH` is OVERFLOW and the host refuses it. The
  `rr ila capture` defaults are inside the limit.
- Qualifier builds store one cycle late (REA-P2.11, ip 1.5.1): with
  `G_QUAL_CONDS > 0` the capture FSM runs one sample cycle behind the probe
  so the qualifier can be a flop. Window, PRETRIG_VALID, pointers and
  timestamps are unchanged; writes, STATUS and trigger_out move one cycle.
  A cycle-exact testbench on such a build compares against the reference
  delayed one register (see `rr_rea_qual_lockstep_harness`'s `u_refd`), and
  ignores STATUS for one cycle after the arm.
- REA-T2.5: before ip 1.4.1, `PRETRIG_VALID` could vouch for 1-2 of those
  overwritten cells, and its since_arm - fire_lag chain capped Fmax at
  136-156 MHz on a -1 7-series.

## RTL / VHDL style

The existing RTL is the reference — match it. Salient conventions:

- **VHDL-2008**, one entity per file, a shared `rr_rea_pkg` package of types /
  constants / register addresses. SPDX header (`MIT`) on every file.
- `library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;` with
  the **indented-`use`** convention. `library work; use work.rr_rea_pkg.all;`.
- **Generics `G_`-prefixed** (`G_SAMPLE_W`, `G_DEPTH`, `G_TRIG_CONDS`).
  Internal constants `C_`-prefixed. Registered signals `_r`-suffixed.
- Every design unit's header comment cites its `requirements.yml` REQ IDs
  (`REA-REQ-N`). Contract-first: extend `requirements.yml` before the RTL.
- **Register addresses are single-sourced in `rea_regbank.yml`.** The
  `C_REGBANK_ADDR_*` constants in `rr_rea_pkg.vhd` live in a generated
  `REGBANK_ADDRESSES` marker block — regen via `rr regbank generate`, never
  hand-drift. Validate with `rr regbank validate rea_regbank.yml --strict`.

### Rising edge only — no `falling_edge` unless completely justified (HARD RULE)

**Clock logic on the rising edge only.** Never introduce `falling_edge(clk)` or a
dual-edge design (some FFs rising, some falling) **unless it is completely
justified and documented inline** — a genuine, unavoidable protocol requirement,
not a convenience or a hold-margin trick.

Dual-edge clocking puts the clock's *duty cycle* into your timing budget,
doubles the STA edges to constrain, and maps poorly onto real fabric. When a
hold path is tight, **fix it with a timing constraint** (`set_clock_uncertainty`
hold pad, proper CDC, pipelining) — never by moving the source FF to the other
edge.

**The scar (REA-T1.1, 2026-07-23):** `rr_rea_jtag_iface` once registered TDO on
the **falling edge** of tck as an RTL-level hold-margin hack for the Arria-10
SLD-hub readback corruption (RTL-P1.96). It was dual-edge *and* it did not hold
up in silicon (REA readback stayed faulty). The corruption is an intra-tck
HOLD-slack problem; the proven fix is the SDC hold pad on the SLD domain, and the
RTL-correct readback is plain combinational `tdo <= sr(0)` — the vendor BSCAN /
`sld_virtual_jtag` primitive owns pin-level 1149.1 TDO timing. A legitimate
falling-edge use (rare) MUST carry an inline comment stating the unavoidable
reason and why no rising-edge/SDC alternative works.

## Linting

Pinned profile: **`esa-vhdl-strict-provisional`** (declared in `project.yml`
`linting.profile`). Run: `rr linting --profile esa-vhdl-strict-provisional
--src rtl`. New RTL (the v0.8 trust tier onward) is held strict from line one;
pre-existing findings on the shipped v0.7 RTL are a tracked triage backlog, not a
land-blocker — do not mass-rewrite field-proven RTL to satisfy the linter without
a ticket.

## The verification gate (REA-T2.3) — ARM IT ON EVERY CLONE

Because the drift gate is gone, nothing outside this repo verifies its RTL. The
gate is therefore ours, and it has three layers:

| Layer | What runs | Armed by |
|---|---|---|
| pre-push | `pytest tests/` + the full cocotb suite | `git config core.hooksPath .githooks` |
| CI | same, on every push to main, every PR, every `v*` tag | `.github/workflows/ci.yml` (automatic) |
| | ⚠ the CI **sim** job is BLOCKED on RTL-T1.26 — see below | |
| publish | `rr pkg publish` refuses a red suite for `routertl/*` | routertl's `_prepublish_sim_gate` |

**`core.hooksPath` is LOCAL git config — it is not cloned.** A fresh clone has
NO pre-push gate until you run the command above, and the repo will look gated
when it isn't. Check with `git config core.hooksPath` before trusting it.

**CI's sim job is red until a routertl release ships RTL-T1.26**, and that is
deliberate rather than hidden. A published routertl wheel is Cython-compiled,
and engine-script dispatch resolved scripts by literal `.py` filename — so on a
runner (no source tree on the walk-up path) every dispatch missed: hooks
skipped, EMPTY compile order, `make: Nothing to be done for 'sim'`, all 39
tests red. It cannot reproduce on a dev bench, which is why it went unseen.
Fixed in routertl main; the job preflights for it and fails with that reason
instead of a wall of unexplained failures, and goes green on its own once a
release lands. **The pre-push hook is the live behavioural gate meanwhile** —
and it is a real one: it ran during the actual pushes that landed this work.

Why the whole suite and not a fast subset: it takes ~50 s, and a curated subset
silently stops covering every test file added after it was written.

**Scar (2026-08-06).** `C_REA_VERSION` was bumped `0x52454107` → `0x52454109`
and three test files kept the old literal. The suite sat RED for weeks because
nothing ran it. `tests/test_version_magic_single_source.py` is the cheap
structural backstop for that specific class; the gate above is the real fix.

## Simulation

- `rr sim run <test>` (ROUTERTL-001 sanctioned engine); every test ends with
  `engine.simulation.run_simulation(...)`. `rr sim run --all` for the suite.
- **Pass `--wait` from any script, hook or CI job** (RTL-T1.25). Under
  `RR_QUEUE_SIMS=1` a bare `rr sim run` SUBMITS to the queue and exits 0, so
  `if ! rr sim run X` reports a pass for a test that never ran. Measured: an
  `assert False` test exited 0 with the knob on and 1 with it off.
- Hard-coded expected values only (ROUTERTL-002) — never derive expectations
  from the DUT's own inputs at runtime.
- `rr sim coverage-map` enforces every `@requires(REA-REQ-N)` maps to a test.
- **Bug-finding-is-success**: a green adversarial run that finds nothing is a
  yellow flag. Never weaken an assertion or hack the BFM/DUT to make a test pass
  — diagnose first, file the defect (test-diagnosis-first).

## Formal

Trust-tier invariants carry `formal:` PSL blocks in `requirements.yml`
(`rr formal run --ip . --contract`). Every property is subject to the
**anti-vacuity gate** — prove it yields a COUNTEREXAMPLE when tightened before
trusting a PASS. A vacuous green on a trust feature is the exact failure this
tier exists to kill.
