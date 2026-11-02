# rr_rea_scoped.sdc — REA's own timing constraints for Intel/Altera Quartus
# (REA-P2.12), the Quartus twin of rr_rea_scoped.xdc (REA-P2.8).
#
# Applied by a RouteRTL consumer automatically: ip.yml build.sources.sdc ->
# ip.lock scoped_constraints (kind: sdc) -> PACKAGE_SDC_FILES, appended after the
# consumer's own SDC on intel/altera builds (RTL-P3.1331). Quartus has no
# `read_xdc -ref` analogue, so this file is applied GLOBALLY and scopes itself:
# every selector below is keyed on REA's own entity names (rr_rea_sync_word,
# rr_rea_pulse_xfer), which match in both the Quartus Standard and Pro netlists
# and cannot catch another IP's synchronizers. Outside RouteRTL, add it with
# `set_global_assignment -name SDC_FILE rr_rea_scoped.sdc` after your own SDC.
#
# 1) Declare the JTAG clock. Measured 2026-09-26 on an rr_rea_intel build
#    (DE25-Standard, A5ED013BB32AE4SCS, Quartus Pro 25.3.1): Quartus does NOT
#    constrain the sld_virtual_jtag clock on its own. report_ucp lists
#    "Unconstrained Clocks: 1" and the clock status "altera_reserved_tck ...
#    Unconstrained", with warning 332060 ("determined to be a clock but was
#    found without an associated clock assignment"). Without this the whole
#    JTAG domain is untimed. 33.333 ns = 30 MHz, the same ceiling the Xilinx
#    twin uses; a faster cable needs a faster period here. Skipped when the
#    consumer already clocks the port, so a board-level JTAG constraint wins.
set _rr_rea_tck_port [get_ports -nowarn {altera_reserved_tck}]
if {[get_collection_size $_rr_rea_tck_port] > 0} {
    set _rr_rea_tck_clocked 0
    foreach_in_collection _rr_rea_clk [get_clocks -nowarn *] {
        foreach_in_collection _rr_rea_tgt [get_clock_info -targets $_rr_rea_clk] {
            if {[get_node_info -name $_rr_rea_tgt] eq "altera_reserved_tck"} {
                set _rr_rea_tck_clocked 1
            }
        }
    }
    if {!$_rr_rea_tck_clocked} {
        create_clock -name {altera_reserved_tck} -period 33.333 $_rr_rea_tck_port
    }
} elseif {[info commands post_message] ne ""} {
    post_message -type warning "rr_rea_scoped.sdc: no altera_reserved_tck port in this design, so the REA JTAG clock is not declared here. An rr_rea_intel build always has one; if REA runs from an external register bus (G_REG_IFACE = external), bound its crossings against your register clock instead (REA SPEC 'Clock-domain crossings')."
}

# 2) Bound every tck <-> sample_clk crossing into a synchronizer first stage
#    (rr_rea_sync_word / rr_rea_pulse_xfer `s1`) to HALF the faster of the two
#    clock periods: the Xilinx twin's set_max_delay -datapath_only rule, written
#    in the Quartus CDC idiom. Quartus has no -datapath_only, so the default
#    inter-clock setup/hold relationship is removed with set_false_path and the
#    datapath is bounded with set_net_delay (the first stage is driven straight
#    from a source-domain flop, REA-REQ-961, so the net IS the datapath). The
#    bound is derived from the live clocks, never a literal. NOT
#    set_clock_groups: a clock group waives the crossing without bounding it.
#    Multi-bit words are quasi-static (write while disarmed, then arm;
#    REA-REQ-959); section 3 bounds the skew across each word.
set _rr_rea_s1 [get_registers -nowarn {*|rr_rea_sync_word:*|s1* *|rr_rea_pulse_xfer:*|s1*}]
if {[get_collection_size $_rr_rea_s1] > 0} {
    set_false_path -from [get_registers *] -to $_rr_rea_s1
    set_net_delay  -from [get_registers *] -to $_rr_rea_s1 -max \
        -get_value_from_clock_period min_clock_period -value_multiplier 0.5
} elseif {[info commands post_message] ne ""} {
    post_message -type warning "rr_rea_scoped.sdc: matched 0 REA synchronizer first stages (*|rr_rea_sync_word:*|s1*, *|rr_rea_pulse_xfer:*|s1*). Every REA build has them, so the netlist naming has changed and the REA clock-domain crossings are UNBOUNDED on this build."
}

# 3) Bound the skew across each multi-bit word (REA-P1.4). A word crosses per
#    bit (rr_rea_sync_word), and the FSM latches it only after the ARM toggle
#    has crossed through rr_rea_pulse_xfer, two destination edges or more
#    after the write (REA-REQ-959). The bits of one word therefore have to land
#    within one destination clock period of each other: then s2 holds the
#    whole new word one cycle after its last bit, before the arm reaches it.
#    The bound is 0.8 x the FASTER of the two periods, read from the live
#    clocks, never a literal; the 0.2 is margin for clock uncertainty. The
#    faster period is never longer than the destination period, so the
#    requirement above holds. A bound of 0.8 x the destination period alone
#    is analysed by the Timing Analyzer, but Design Assistant still reports
#    CDC-50007 when it exceeds the source period (DE25, sample clock ->
#    altera_reserved_tck: 26.666 ns against a 10 ns source, 4 words). One
#    set_max_skew per word instance: a single constraint over every s1 would
#    bound unrelated words, and both directions, against each other. Quartus
#    Design Assistant rule CDC-50007 flags a multi-bit synchronizer bus without
#    a skew bound; on the DE25 build (REA 1.12.3, Quartus Pro 25.3.1) it
#    listed 14 REA words before this section. rr_rea_pulse_xfer and 1-bit
#    words carry one bit and need no skew bound.
set _rr_rea_words [dict create]
foreach_in_collection _rr_rea_reg [get_registers -nowarn {*|rr_rea_sync_word:*|s1*}] {
    set _rr_rea_name [get_node_info -name $_rr_rea_reg]
    if {[regsub {\|s1(\[[0-9]+\])?$} $_rr_rea_name {} _rr_rea_inst]} {
        dict incr _rr_rea_words $_rr_rea_inst
    }
}
set _rr_rea_skewed 0
dict for {_rr_rea_inst _rr_rea_bits} $_rr_rea_words {
    if {$_rr_rea_bits < 2} {
        continue
    }
    set _rr_rea_word_s1 [get_registers -nowarn \
        "[string map {\[ \\\[ \] \\\]} $_rr_rea_inst]|s1*"]
    if {[get_collection_size $_rr_rea_word_s1] != $_rr_rea_bits} {
        if {[info commands post_message] ne ""} {
            post_message -type warning "rr_rea_scoped.sdc: the REA word $_rr_rea_inst has $_rr_rea_bits first-stage bits, but its pattern selects [get_collection_size $_rr_rea_word_s1]; its skew is NOT bounded on this build."
        }
        continue
    }
    set_max_skew -from [get_registers *] -to $_rr_rea_word_s1 \
        -get_skew_value_from_clock_period min_clock_period \
        -skew_value_multiplier 0.8
    incr _rr_rea_skewed
}
if {[info commands post_message] ne ""} {
    post_message -type info "rr_rea_scoped.sdc: max skew bounded on $_rr_rea_skewed multi-bit REA words (0.8 x the faster period)."
}
