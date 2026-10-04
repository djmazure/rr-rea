# rr_rea_xilinx7_scoped.xdc: REA-P2.19. Applied `read_xdc -ref rr_rea_xilinx7`
# (an ip.yml {path, ref} entry, RTL-P2.1532), so every get_* resolves inside
# the BSCANE2 wrapper wherever the consumer instantiates it. A Xilinx design
# that does not use this wrapper (e.g. the AXI-Lite variant) has no
# rr_rea_xilinx7 module: Vivado then skips the file with CRITICAL WARNING
# [Designutils 20-1281] and the build continues.
#
# BSCANE2's CAPTURE/SHIFT/UPDATE/SEL/TDI outputs are unclocked startpoints:
# Vivado timed none of the paths from them into the TAP logic (Slack inf,
# Path Group (none); REA-P2.19 baseline). They change on TCK, so each path
# is bounded at half the TCK period declared in rr_rea_scoped.xdc (33.333 ns).
set_max_delay -datapath_only 16.667 \
  -from [get_pins {u_bscane2/CAPTURE u_bscane2/SHIFT u_bscane2/UPDATE u_bscane2/SEL u_bscane2/TDI}]
