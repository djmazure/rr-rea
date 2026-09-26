# REA-P2.14 — silicon re-witness of rr-rea 1.9.0 on the bench families

Every earlier REA silicon witness predated rr-rea 1.5.x. This one re-runs the
on-silicon battery (routertl `examples/rea_source_demo/tools/rea_board_battery.sh`,
RTL-P2.1097) on the current RTL. For each board it records the rr-rea version,
the bitstream identity, the capture artifacts and the power-audit ledger.

- **RTL under test:** rr-rea `0e30ea5` (ip 1.9.0), consumed as the `routertl/rea`
  package. The demos pin it by commit (RTL-P2.1399), and `rr pkg install` reported
  every `ip.lock` unchanged.
- **Demos and flow:** routertl `052df391` for kv260, zybo_classic and polarfire;
  `ed08f52a` for de25, which needed the RTL-P2.1404 SDC fix. Every bitstream was
  built through `rr queue` from the author's own worktree, so each bit identity
  is traceable to one build.
- **Bench discipline:** each board ran under an `rr lease` held by
  `claude-opus-5-5/rea-p2-14`, one board at a time, and was powered off
  afterwards. Power-off was verified by an external probe, never by the PDU's
  acknowledgement alone.
- **Date:** 2026-09-26.

## Result

| Board (part) | Build job | Bitstream sha256 | Identity readback | Battery |
|---|---|---|---|---|
| KV260 (xck26) | `413c62dfe3df` | `ab24a6ac…0967` | bit-sha **MATCH** `0x77fa0945` (CONFIRMED) | ALL CHECKS PASSED |
| Zybo Classic 1 (xc7z010) | `9d0c615048de` | `3cefb9bc…1dcc` | bit-sha **MATCH** `0x79a5af86` (CONFIRMED) | ALL CHECKS PASSED |
| DE25-Standard (Agilex 5, A5ED013BB32AE4SCS) | `b37552aa6cd3` | `9ed837a4…6c3a` | UNCONFIRMED (no Altera readback, RTL-P3.1528) | ALL CHECKS PASSED |
| PolarFire SoC Discovery (MPFS095T) | `c036d1965c63` | job `061e584d…863e` | Fabric digest `3ab26abd…902b` programmed = build digest; device readback UNCONFIRMED (no Microchip USR_ACCESS) | ALL CHECKS PASSED (counter profile) |

Every board read VERSION `0x5245410D` (the rr-rea 1.9.0 magic) and BUILD_ID <!-- rea-magic:historical -->
`0x903A93E9`. FEATURES was `0x00080B04` on the three source-demo boards and
`0x00080104` on PolarFire, whose demo has one SOURCE bit. The battery's
word-exact selftest passed with CRC `0xbd3bcf60` on all four. The
source-profile checks, identical on KV260, Zybo Classic 1 and DE25:

- **Trigger:** two captures (direction 0 and 1), 4096 samples each, with
  exactly one trigger marker at pretrigger index 1024.
- **SOURCE landed:** the post-trigger windows are strictly incrementing and
  decrementing (3071/3071 steps each).
- **Readback:** `direction_q` reads back the registered SOURCE bit the counter
  saw.
- **Live capture:** the two captures differ, and `free_tick` toggles throughout.

The battery prints "bit-sha not confirmed" on the Xilinx boards because it runs
bit-sha without `--cable` and treats any failure as expected (RTL-P2.1405). The
MATCH lines above come from `rr hw bit-sha --cable <serial>` run by hand after
programming; see `*/…bitsha.txt`.

## Per board

### KV260 — `kv260/`

- **Power:** ledger `10:19:39Z on` / `10:21:59Z off`. Off verified: `xck26`
  gone from the JTAG chain.
- **bit-sha:** a plain `rr hw bit-sha` refused, because the demo's
  `runtimes/hw.yml` redefines the host bench target (RTL-P2.1405). It was run
  with `--cable XFL1F0VLP1OI`.

### Zybo Classic 1 — `zybo_classic/`

- **Why this board:** the owner moved the 7-series leg here (2026-09-26), since
  zybo-z7-20 had no power.
- **First attempt (`10:22:59Z on` / `10:24:16Z off`):** the lease gate refused
  `rr program run --cable 210279539777`, because platform `zybo` is ambiguous
  (RTL-P2.1406). Nothing was programmed, and neither the gate nor the demo was
  bypassed.
- **Witnessed run (`11:05:29Z on` / `11:06:56Z off`):** after RTL-P2.1406
  landed (routertl `7a155eec`, lease-guard only; the demo tree was unchanged).
- **Off verified:** the FT2232 `210279539777` was gone from USB.

### DE25-Standard — `de25/`

**Build.** The first build on `052df391` failed signoff (job `72cd7e5bffb4`,
CLOCK0_50 −15.002 ns). The demo SDC's `set_output_delay … [all_outputs]` timed
the SDM JTAG pin `altera_reserved_tdo` against CLOCK0_50 (RTL-P2.1404). The
rebuild on `ed08f52a` passes signoff:

| Check | Slack |
|---|---|
| CLOCK0_50 setup | +16.346 ns |
| `altera_reserved_tck` setup | +11.960 ns |
| hold | +0.091 ns |
| recovery | +17.568 ns |
| removal | +0.167 ns |

**Programming.**
- Two attempts failed with "Programming hardware cable not detected": rr ran
  the Std `quartus_pgm`/`jtagconfig` found on PATH instead of the project's Pro
  `tool_path`, and the Std jtagd cannot see the USB-Blaster III (RTL-P2.1407).
  Logs are kept as `de25.program.attempt*.log`.
- The two Std jtagd processes were this session's own, stopped by PID.
- The witnessed run was the same `rr program run` with
  `PATH=/opt/altera_pro/25.3/quartus/bin:$PATH` (environment only), which
  reported "Configuration succeeded at device index 1".

**Power.** Ledger `11:39:42Z on` / `11:50:29Z off`. Off was verified by the Pro
`jtagconfig` ("Unable to read device chain - JTAG chain broken"; the blaster is
bus-powered and stays on USB) and by the PDU reading outlet 5 OFF.

### PolarFire SoC Discovery — `polarfire/`

**Package wrapper — PROVEN from the build** (the REA-P3.9 checkpoint). The
Libero log imports `libs/routertl/rea/rtl/rr_rea_jtag_microchip.vhd` into
library `rr_rea`, with nothing from `src/units`. Three copies of the wrapper
share sha256 `626e7e33…8168`:

- the package copy;
- Libero's `project/hdl` import copy;
- `rr-rea@0e30ea5:rtl/rr_rea_jtag_microchip.vhd`.

See `polarfire.wrapper-provenance.txt`. SmartTime met.

**Job contents.** From the job's Libero log (`polarfire.job-contents.txt`) and
`SNVM.cfg`:
- "Successfully generated bitstream file and it has been added to the job
  container; file programs Fabric and sNVM."
- "Bitstream not generated for eNVM. The configuration file: is either absent,
  unreadable, or empty."

The sNVM content is Libero's automatic design-initialisation data:
`INIT_STAGE_1_SNVM_CLIENT` (4368 bytes from page 202) and
`INIT_STAGE_2_3_SNVM_CLIENT` (32 bytes at page 0). Deepskopion's own rr-built
job writes the same two init clients at the same pages. The owner approved
fabric + sNVM for this run (eNVM/HSS untouched). A fabric-only export is
RTL-P3.1844.

**First attempt.** This session's permission gate stopped the programming
step. The board was leased and powered on, nothing was written, and it was
powered off again (`10:35:03Z on` / `10:35:42Z off`). With the user's approval
the run went ahead.

**Witnessed run (`12:08:24Z on` / `12:11:16Z off`).** The demo project itself
was programmed (not a `-b` path; RTL-P1.148), from `12:08:29Z` to `12:10:40Z`.
`polarfire.program.log` shows:
- "Programming FPGA Array and sNVM...";
- Fabric digest `3ab26abd55699cec0bdee00400188c36662bbfd840f1a22c407cbb8c4af7902b`
  and sNVM digest `004f0cf648e9c205d41f501dff28f234c389280e7420e93d4823c715dd3aee2a`,
  identical to the digests of build job `c036d1965c63`;
- "Bitstream not generated for eNVM";
- "Executing action PROGRAM PASSED".

**Battery (counter profile).**
- 4096 samples over the UJTAG/FlashPro5 path.
- Exactly one trigger marker.
- The free-running 8-bit counter steps +1 on every sample (4095/4095).
- Selftest word-exact, CRC `0xbd3bcf60`.

This is the **first silicon capture through the package-shipped
`rr_rea_jtag_microchip`**. Both earlier PolarFire SoC witnesses (DS-P2.132,
DS-P2.128) used a copy kept outside the package.

**Power.** Off verified: the FlashPro5 `03ASSOGW` was gone from USB.

**Side effect.** The kit fabric was overwritten with REA demo job
`c036d1965c63` at 2026-09-26T12:10:40Z; it had been deepskopion fabric
`34119f48`. This is recorded in the deepskopion context. Deepskopion's
PolarFire runs program their own image first.

## Defects found (filed)

| Ticket | What |
|---|---|
| RTL-P2.1404 | DE25/Arria 10 demo SDCs timed the JTAG TDO pin as board I/O. Fixed in `ed08f52a`. |
| RTL-P2.1405 | The battery reports a failed Xilinx bit-sha as expected and does not pass `--cable`. The kv260 demo `hw.yml` redefines the host bench target. |
| RTL-P2.1406 | The zybo_classic demo could not be programmed through the lease gate (ambiguous `zybo`). Fixed in `7a155eec`. |
| RTL-P2.1407 | `rr` Altera hardware commands use PATH Quartus (Std) instead of the project's Pro `tool_path`. |
| RTL-P3.1844 | Microchip program flow has no fabric-only job export. |
