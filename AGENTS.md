# AGENTS.md — rr_rea (RouteRTL Embedded Analyzer)

Repo-specific conventions for `rr-rea`, the first-party embedded logic-analyzer
IP. REA **graduated** from a routertl-vendored IP to a standalone registry IP
(`rr pkg add routertl/rea`) — this repo is its canonical home. It is developed
and **tested standalone**: tests resolve this repo's own `rtl/` (never a
vendored `ip/routertl/rea/` path — that fossil was removed 2026-07-23). The SDK
carries a byte-identical builtin copy under `routertl/ip/routertl/rea/`; RTL
changes here must be synced there.

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

## Simulation

- `rr sim run <test>` (ROUTERTL-001 sanctioned engine); every test ends with
  `engine.simulation.run_simulation(...)`. `rr sim run --all` for the suite.
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
