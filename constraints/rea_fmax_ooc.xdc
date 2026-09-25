# REA-P2.9 — out-of-context Fmax characterization of rr_rea_top.
#
# Measurement constraint for the targets/util_*.yml points, NOT a board
# constraint: it has no pinout. sample_clk_i is the probed design's clock and
# the one a high-speed integration cares about, so it gets a deliberately
# aggressive period (400 MHz); Fmax = 1000 / (period - WNS) from the routed
# timing summary. The JTAG and register-interface clocks are slow in every
# real integration and only need to be timed, not pushed.
#
# CDC: nothing here. The crossings and the TCK clock come from REA's SHIPPED
# constraint, constraints/rr_rea_scoped.xdc (REA-P2.8), so this measurement
# uses exactly what a consumer gets. It is read after this file
# (alphabetical), once sample_clk exists for its derived bound.
create_clock -name sample_clk -period 2.500 [get_ports sample_clk_i]
