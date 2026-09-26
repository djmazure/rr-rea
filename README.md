# rr_rea — RouteRTL Embedded Analyzer (REA)

Vendor-portable on-chip logic analyzer IP, JTAG-attached. VHDL, MIT-licensed.
This repository is the public home of the `routertl/rea` package on
[registry.routertl.dev](https://registry.routertl.dev).

## What it is

- Sliding-window capture from reset deassertion (no uninitialized pre-trigger
  cells), value/mask + mixed-op comparator-array triggers (`==` `!=` `<` `>`
  rising/falling), an external board-pin trigger, decimation, write-side SOURCE,
  and a content-fingerprint identity block (`VERSION` / `FEATURES` /
  `BUILD_ID`). The capture FSM also carries a multi-stage sequencer, but it is
  not wired to `rr_rea_top` or the host yet (REA-P3.7), so a sequence trigger
  cannot be armed.
- **Storage qualification** (v0.11, `G_QUAL_CONDS > 0`): store a sample only
  when a qualifier holds, so a 4096-deep window holds 4096 bus *events* spread
  over seconds instead of 82 us of idle cycles at 50 MHz. The qualifier uses
  the comparator-array slot encoding (EQ/NE/RISE/FALL on a probe field, AND or
  OR across slots), and the timestamp plane (mandatory with a qualifier) places
  each stored event in time. Off by default; with `G_QUAL_CONDS = 0` the core
  is the v0.9 analyzer bit for bit. See `SPEC.md` "Storage qualification".
- Vendor JTAG wrappers for Xilinx 7-series (`rr_rea_jtag_xilinx7`, BSCANE2),
  Intel/Altera (`rr_rea_jtag_intel`, `sld_virtual_jtag`) and Microchip
  PolarFire / PolarFire SoC (`rr_rea_jtag_microchip`, UJTAG), selected
  per-vendor by the package manifest. Silicon-proven on Zybo Z7-20 (Zynq-7000),
  DE25-Standard (Agilex 5) and the PolarFire SoC Discovery Kit (MPFS095T).
- Host side lives in the [RouteRTL](https://pypi.org/project/routertl/) SDK:
  `rr ila capture --core <name>`, `rr ila identity`, RouteWave wave viewer.

## Use it

```bash
pip install routertl
rr pkg add routertl/rea      # in your project directory
```

The manifest carries an `ip.yml build.hooks` contract: the consumer build
auto-generates `rr_rea_build_id_pkg.vhd` (a hash of the declared sources) into
your project's `generated/` dir and places it in library `rr_rea` — no manual
wiring. See `SPEC.md` for the register map and integration contract.

## Status of storage qualification (REA-P2.7)

| Proven | How |
|---|---|
| Qualification off is the v0.9 analyzer, cycle for cycle | 30 000-cycle lockstep against a frozen copy of the v0.9 FSM (`test_rea_storage_qual_p2_7`) |
| Qualifier ops, AND/OR, empty and unsupported slots, arm-time latching | per-cycle model check on `dpram_we_o` |
| Sparse events fill the window; trigger position, `PRETRIG_VALID`, decimation | 1-in-~1000-cycle events, FSM and full JTAG top |
| Timestamps carry the real gaps between stored events | JTAG top, testbench-measured cycles |
| Illegal builds (qualifier without timestamps, > 15 slots) halt elaboration | nvc elaboration test |
| 14 RTL mutations each turn the suite red | mutation battery (recorded in the landing notes) |

Not yet proven: silicon (the rr-openpiton KV260 UART capture is the first
field case). Host support landed in the RouteRTL SDK with RTL-P2.1373
(routertl `a9013711`, 2026-09-25): `rr ila capture --qualify` /
`--qualify-any`, `storage_qualifier:` in `debug/*.yml`, and CSV/VCD exports
placed by timestamp. The live RouteWave view still places samples by index
(RTL-P2.1385). Resources: the qualifier is `G_QUAL_CONDS` equality/edge reducers
over the probe plus `G_QUAL_CONDS x (2 x G_SAMPLE_W + 5)` config flops and
their synchronizers; no RAM, no DSP.

**Cost and Fmax per configuration** (LUT, FF, BRAM tiles and routed Fmax for
five build points from 8 x 1K to 256 x 8K, and the BRAM aspect-ratio rule
that predicts them) are in SPEC "Resources and Fmax per configuration". The
contract (`technical:` in requirements.yml) caps the default elaboration.

## For agents

`AGENTS.md` opens with a capability summary: what REA can and cannot do,
which generic enables what, the constraints it ships, and the open defects to
design around. `SPEC.md` is the contract behind every line of it.

## Verify

Contract-first: every requirement in `requirements.yml` is exercised by a
tagged cocotb test under `sim/cocotb/tests/` (run via `rr sim run <test>`
from a checkout; `rr sim coverage-map` enforces the REQ↔test mapping).

## License

MIT — see `LICENSE`. The RouteRTL SDK itself is separately licensed.
