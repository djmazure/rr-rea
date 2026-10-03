# Guarded-DR discriminator image (REA-P2.20)

A debug-only reference top for an Arria 10 board, used to test one hypothesis
about the RTL-P2.901 readback fault. On that fault, every register value with
bit0 = 1 reads back as an all-ones DR, while even values read exactly. The
hypothesis: the trigger is the first bit shifted out after CAPTURE being 1. The
guarded DR (`G_DR_GUARD`, REA-REQ-968) makes that first bit a constant 0.

It is a hypothesis, not a named mechanism. The image exists to refute or
confirm it.

## What it contains

`rtl/rr_rea_dr_guard_discriminator_top.vhd` instantiates three `rr_rea_intel`
cores and no design logic. The package ships it as an Altera-only synthesis
source, so it is unused unless a target names it as its top.

| Core | `G_SAMPLE_W` | `G_DR_GUARD` | Role |
|---|---|---|---|
| A | 256 | false | positive control: must show the fault |
| B | 256 | true | treatment |
| C | 12 | false | width control (reported, never decisive) |

Ports: `clk_i`, any free-running board clock (the probe content does not
depend on its frequency, so there is no frequency generic), and `trigger_o`,
the OR of the three cores' `trigger_o`. Generics `G_IDX_A/B/C` set each core's
`sld_instance_index` (defaults 1, 2, 3).

`trigger_o` must reach a real pin (an LED is fine). With no observable output
Quartus prunes each core's capture path. The readback would still work, but
the placement would no longer resemble a real REA build, and the fault is
placement-dependent.

## How to build it (pure rr flow)

A consuming target declares only the board facts. The `rr schema` keys:

- `project.top_module: rr_rea_dr_guard_discriminator_top`, or the consumer's
  own thin wrapper (below).
- `packages: {"routertl/rea": "^1.11.1"}`, or `commit:<sha>` before the tag is
  published.
- `hardware.vendor` / `hardware.part` / the tool version, as for any target.
- `hardware.pins`, or pins inferred from the top's port names through the
  board pinout. The names here are neutral (`clk_i`, `trigger_o`). If the
  board's pinout calls them something else, add a two-port wrapper entity in
  the consumer repo that instantiates this top, and point `top_module` at it.
  Board names never go into rr-rea.
- `sources.sdc` or `paths.constraints`: a `create_clock` on the board clock
  port. REA's own crossings and the `altera_reserved_tck` clock come from the
  package's shipped `rr_rea_scoped.sdc`; do not redeclare them.

Then build through the queue:

```bash
rr queue submit synth && rr queue submit impl && rr queue submit bitstream
```

No hand Tcl and no vendor IP catalog.

## How to read it

Through `rr ila`, never a standalone System Console script:

- core B needs the guard flag (`dr_guard: true` in its `debug/<core>.yml`,
  RTL-P2.1527); FEATURES[31] reads 1 on B and 0 on A and C, and the host
  refuses a flag that disagrees;
- the sweep is `rr ila readback-map` (RTL-P2.1528): 32 VERSION reads, FEATURES,
  and a write/read-back of every value 0x00..0xFF plus the VERSION magic and
  its bit0-cleared twin (the readback-map defaults)
  through PRETRIG, each classified exact / all-ones / other.

The verdict table is fixed in REA-P2.20 before the run.
