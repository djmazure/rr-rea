-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rea_tap_mock_pkg — sim-only stand-in for the TAP state a vendor JTAG
-- primitive would expose (REA-P3.6). The BSCANE2 and sld_virtual_jtag mocks
-- read these global signals and the wrapper harness drives them from its own
-- ports, so a cocotb test can scan the REA register bus THROUGH the real
-- vendor wrapper. Compiled into library rea_tap_mock. Never synthesised.

library ieee;
    use ieee.std_logic_1164.all;

package rea_tap_mock_pkg is
    signal tap_tck     : std_logic := '0';
    signal tap_tdi     : std_logic := '0';
    signal tap_tdo     : std_logic := '0';
    signal tap_capture : std_logic := '0';
    signal tap_shift   : std_logic := '0';
    signal tap_update  : std_logic := '0';
    signal tap_sel     : std_logic := '0';
end package;
