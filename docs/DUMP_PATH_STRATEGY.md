# REA dump-path strategy — faster readback without an Ethernet analyser

**Status:** approved plan, wave-ready epic
**Date:** 2026-08-15
**Source conversation:** FPGA-company PM eval follow-up (JTAG vs Ethernet)

**The dependency graph in tich-pm is the live source of dispatch order.**
This document is the spec the tickets cite. Do not invent a second
analyser, a second register map, or a MAC inside `rr_rea`.

Category / wave-scope key: **`ReaDumpPath`**.
`--ready-from rr-rea --ready-cat ReaDumpPath` and
`--ready-from routertl --ready-cat ReaDumpPath` together are the epic.
(Host tickets live in `routertl`; RTL tickets live in `rr-rea`.)

---

## Verdict (frozen)

JTAG stays the default door and the 90 % bring-up path. Ethernet (and AXI)
are **trucks for the window after `STATUS.done`**, not a second REA.

- Capture rate is `sample_clk` into BRAM. JTAG does not limit it.
- JTAG limits **dump latency**. Zybo Z7-20, DEPTH=4096: ~1.9 s batched
  (SPEC v0.2). A 704-bit probe is ~22× the 32-bit pages.
- `G_REG_IFACE => "external"` + `rr_rea_axi4lite` already is the other
  door (RTL-P3.931 / LEAN_PROFILE.md). The host still only speaks JTAG.
- Two live masters on one register bus remain forbidden in v1
  (REA-REQ-901). Dual-door is a later, explicit arbiter — not “wire both”.
- Continuous `sample_clk` over the wire is a sniffer (Deepskopion /
  `rr-ddr-stream`), not this epic.

## Transports

| Name | Moves | v1 | Notes |
|---|---|---|---|
| `jtag` | 32-bit register bus via TAP | **exists** | Default. Always sufficient for arm / status / small dump. |
| `axi_lite` | same 32-bit map via AXI4-Lite / mmap / UIO | **this epic** | Zynq/HPS: CPU + existing GbE is the Ethernet flavour. No FPGA MAC. |
| `axi_stream_window` | packed dump of `DATA_BASE` after `done` | **this epic** | Burst. Control stays register-map. |
| `udp_window` | same packed window over a proven MAC | **Icebox** | Bare FPGA, no PS. Packetizer only. Not inside `rr_rea`. |

The register map in SPEC.md is the protocol. A transport only moves
addr/data or a window blob. `wave_stream_v1` remains the host → RouteWave
seam. Contract: SPEC.md "Dump-path transports" + REA-REQ-909..913
(REA-P2.4). `FEATURES[20]` advertises `axi_stream_window`, `[21]` is
reserved for `udp_window`; no VERSION bump for a transport.

### Window blob (burst / UDP)

After `STATUS.done`:

- `CAPTURE_LEN` cells. **Decided (REA-P2.4, REA-REQ-912): the burst engine
  emits trigger-at-pretrig order** — blob cell *i* is physical
  `(START_PTR + i) mod DEPTH`, no host rotation for a window transport.
  Register-map transports keep host-side rotation (existing REAClient
  contract). Tested on both doors in `test_rea_dump_path_contract_p2_4`;
  `sim/cocotb/tests/rea_window_blob.py` is the executable reference.
- Plane-major: sample plane, then timestamp plane if `FEATURES[18]`.
- Each cell is `ceil(SAMPLE_W/32)` little-endian 32-bit words, same paging
  as `DATA_WORD_SEL`.
- `tlast` / end-of-datagram on the last beat of the last plane.

## DAG

```
REA-P2.4 contract
  ├─ RTL-P2.1133 REAClient transport iface
  │    └─ RTL-P2.1134 AXI mmap/UIO + `rr ila capture --transport axi`
  │         ├─ RTL-P3.1600 silicon timing (also needs RTL-P3.1599)
  │         └─ REA-P3.4 dual-door arbiter (also needs REA-P2.5)
  ├─ REA-P2.5 AXI-Stream window dump RTL
  │    ├─ RTL-P2.1135 REAClient consumes burst (also needs RTL-P2.1134)
  │    └─ REA-ICE.1 UDP window dump (Icebox; owner-gated)
  └─ RTL-P3.1599 Zynq example: external + axi4lite on PS
       └─ RTL-P3.1600
```

| Key | ID | Repo | Tier | Title |
|---|---|---|---|---|
| T1 | **REA-P2.4** | rr-rea | P2 | Dump-path transport contract |
| T2 | **RTL-P2.1133** | routertl | P2 | REAClient transport interface |
| T3 | **RTL-P2.1134** | routertl | P2 | AXI mmap/UIO transport |
| T4 | **RTL-P3.1600** | routertl | P3 | Silicon: AXI dump vs JTAG dump |
| T5 | **REA-P2.5** | rr-rea | P2 | AXI-Stream window dump RTL |
| T6 | **RTL-P3.1599** | routertl | P3 | Zynq example, external + AXI4-Lite |
| T7 | **RTL-P2.1135** | routertl | P2 | REAClient consumes window burst |
| T8 | **REA-P3.4** | rr-rea | P3 | Dual-door arbiter (JTAG wins) |
| T9 | **REA-ICE.1** | rr-rea | Icebox | UDP window dump, bare FPGA (cat `ReaDumpPathGated` — not on the wave frontier) |

## Hard rules for dispatched agents

- Do not add a MAC, ARP, or TCP stack to `rr_rea`.
- Do not instantiate JTAG and `external` masters together until T8 lands.
- Do not change the frozen 32-bit register map to “make Ethernet easier”.
- JTAG `rr ila capture` on the existing demos must keep working.
- Full-project synth/impl/bitstream goes through `rr queue`. Sims are
  slot-capped. Hardware needs `rr lease`.
- Bug-finding-is-success on sim tickets: a green AXIS dump that never
  compared against `DATA_BASE` is not done.

## Out of scope

- Live streaming every sample_clk beat (sniffer product).
- ngscopeclient / third-party GUI drivers.
- Making JTAG optional on a bring-up board.
