# rr_rea — RouteRTL Embedded Analyzer (REA)

Vendor-portable on-chip logic analyzer IP, JTAG-attached. VHDL, MIT-licensed.
This repository is the public home of the `routertl/rea` package on
[registry.routertl.dev](https://registry.routertl.dev).

## What it is

- Sliding-window capture from reset deassertion (no uninitialized pre-trigger
  cells), value/mask + comparator-array + multi-stage sequencer triggers,
  decimation, write-side SOURCE, and a content-fingerprint identity block
  (`VERSION` / `FEATURES` / `BUILD_ID`).
- Vendor JTAG wrappers for Xilinx 7-series (`rr_rea_jtag_xilinx7`, BSCANE2)
  and Intel/Altera (`rr_rea_jtag_intel`, `sld_virtual_jtag`), selected
  per-vendor by the package manifest. Silicon-proven on Zybo Z7-20 (Zynq-7000)
  and DE25-Standard (Agilex 5).
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

## Verify

Contract-first: every requirement in `requirements.yml` is exercised by a
tagged cocotb test under `sim/cocotb/tests/` (run via `rr sim run <test>`
from a checkout; `rr sim coverage-map` enforces the REQ↔test mapping).

## License

MIT — see `LICENSE`. The RouteRTL SDK itself is separately licensed.
