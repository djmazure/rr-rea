# rr_rea_scoped.xdc — REA's own timing constraints (REA-P2.8), applied by the
# consumer build as `read_xdc -ref rr_rea_top <this file>` (ip.yml
# build.sources.xdc -> ip.lock scoped_constraints, RTL-P2.636). Every get_* is
# scoped to the rr_rea_top INSTANCE, so this resolves wherever and however many
# times the consumer instantiates REA, and whatever its clocks are called.
#
# 1) Declare the JTAG clock. Vivado does NOT derive a clock on a user-
#    instantiated BSCANE2 TCK (measured 2026-09-25: rr_rea_xilinx7 OOC synth,
#    993 register pins "no clock", TCK listed under Unconstrained Clocks), so
#    without this the whole JTAG domain is untimed and "timing met" says
#    nothing about it. 33.333 ns = 30 MHz, the fastest TCK a Digilent/Platform
#    Cable drives; a faster cable needs a faster period here.
create_clock -period 33.333 [get_ports tck_i]

# 2) Bound every tck <-> sample_clk crossing into a synchronizer first stage
#    (rr_rea_sync_word / rr_rea_pulse_xfer `s1`, ASYNC_REG) to HALF the faster
#    clock's period, datapath only. NOT set_clock_groups -asynchronous: a
#    clock group waives the crossing and would hide an unsafe one (REA-P2.10's
#    CDC-10). The bound is derived from the live sample clock, never a literal.
set rr_rea_t_sample [get_property PERIOD [get_clocks -of_objects [get_ports sample_clk_i]]]
set rr_rea_bound [expr {0.5 * min(33.333, $rr_rea_t_sample)}]
set_max_delay -datapath_only $rr_rea_bound \
  -from [get_cells -hierarchical -filter {IS_SEQUENTIAL}] \
  -to   [get_cells -hierarchical -filter {NAME =~ "*u_cdc_*/s1_reg*"}]
