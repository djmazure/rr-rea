# rr_rea_scoped_microchip.sdc — REA's own timing constraints for Microchip
# Libero SmartTime (REA-P2.13), the PolarFire / PolarFire SoC twin of
# rr_rea_scoped.xdc (REA-P2.8) and rr_rea_scoped.sdc (REA-P2.12).
#
# SmartTime reads a restricted, Synopsys-style SDC: no Tcl variables, no expr,
# no if. So, unlike its twins, this file cannot find out whether a clock is
# already declared, and cannot derive the bound from the live sample clock. It
# is a plain three-command file with a literal bound (see 2).
#
# Applied by a RouteRTL consumer automatically: ip.yml
# build.sources.sdc_per_vendor.microchip -> ip.lock -> imported into the Libero
# project on microchip builds only (routertl RTL-P2.1392; an older rr ignores
# it, so list the file under project.yml sources.sdc yourself). Outside
# RouteRTL, add it to Libero's constraint manager for synthesis, place-and-route
# and timing verification.
#
# 1) Declare the JTAG clock on the UJTAG macro's UDRCK output. Measured
#    2026-09-26 (rr_rea_jtag_microchip in a PolarFire SoC Discovery build,
#    MPFS095T, Libero 2025.2): with no REA constraint, SmartTime's coverage
#    report lists the REA JTAG logic under an inferred "jtag_tck" domain with
#    0 of 2524 setup/recovery checks constrained ("Clock constraint is
#    missing"), and rr's timing gate still reports met. With this line: 0
#    unconstrained checks. Selector forms measured in the same build:
#    get_pins {*/u_ujtag/UDRCK} covers the whole 2524-check domain;
#    get_pins {...u_ujtag:UDRCK} is rejected by Libero's synthesis SDC check
#    (SDC0001 "clock source ... is incorrect"); get_nets on tck_s reaches only
#    49 checks. 33.333 ns = 30 MHz, the ceiling the Xilinx and Quartus twins
#    use; the embedded FlashPro5 runs slower. Do not also create a clock on
#    your top-level TCK port: it is the same clock.
create_clock -name {rr_rea_udrck} -period 33.333 [get_pins {*/u_ujtag/UDRCK}]

# 2) Bound every tck <-> sample_clk crossing into a synchronizer first stage
#    (rr_rea_sync_word / rr_rea_pulse_xfer `s1`, instance u_cdc_* under the
#    wrapper's u_top). The `*` before u_cdc_ is load-bearing: a synchronizer
#    inside a generate is named `<generate>.u_cdc_*` (g_qual_cdc.u_cdc_qual_mode
#    when G_QUAL_CONDS > 0), and the first-cut `*/u_top/u_cdc_*` missed it, so
#    that crossing was timed as a plain udrck -> sample_clk path (hold -1.262 ns
#    on MPFS095T, G_SAMPLE_W 80). tests/test_rea_scoped_microchip_sdc_p2_13.py
#    holds every instance to this selector. SmartTime has no -datapath_only, so this bound includes
#    the clock-latency difference between the two domains. The rule elsewhere
#    is half the faster clock period; SmartTime cannot compute it, so the
#    literal 5.000 ns equals that rule for a sample clock of up to 100 MHz and
#    is stricter for a slower one. With a faster sample clock, add your own
#    tighter set_max_delay on the same targets. Measured on the build above: a
#    deliberately impossible 0.05 ns bound made SmartTime report every
#    crossing (so the bound is scored), with D-pin crossings needing about
#    1.9 ns and the jtag-domain async-reset (ALn) paths into the same flops
#    about 3.9 ns. NOT set_clock_groups: it waives the crossing instead of
#    bounding it.
set_max_delay 5.000 -from [get_cells {*}] -to [get_cells {*/u_top/*u_cdc_*/s1*}]

# 3) Remove the hold requirement on the same crossings. Vivado's
#    -datapath_only drops it; SmartTime instead times hold against the latency
#    difference of two unrelated clocks. Measured on the build above with only
#    (1)+(2): 20+ hold "violations", all JTAG-domain register -> s1:D, worst
#    -1.073 ns against a 2.847 ns requirement that is only that latency
#    difference. A synchronizer tolerates any arrival time, so the check is
#    meaningless there. -33.333 (one TCK period) removes it for any clock tree.
#    Libero's synthesis SDC check rejects a pin target here (SDC0023 for both
#    s1*:D and s1*/D), so the target is the s1 cells; the jtag-domain
#    async-reset removal check into those same flops is relaxed with it, which
#    a synchronizer first stage also tolerates. With (1)-(3): 0 of 7557
#    checks unconstrained and no setup or hold violation.
set_min_delay -33.333 -from [get_cells {*}] -to [get_cells {*/u_top/*u_cdc_*/s1*}]
