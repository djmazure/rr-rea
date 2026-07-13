-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Test fixture (RTL-P3.1203): stands in for the build-regenerated
-- rr_rea_build_id_pkg with a KNOWN non-zero C_REA_BUILD_ID, to prove that an
-- injected build id flows package -> rr_rea_top's G_BUILD_ID default ->
-- rr_rea_regbank -> BUILD_ID (0xD4) readback. Compiled INSTEAD of the committed
-- stub in test_rea_build_id_p3_1203.py (same package name, so no collision).

library ieee;
    use ieee.std_logic_1164.all;

package rr_rea_build_id_pkg is

    constant C_REA_BUILD_ID : std_logic_vector(31 downto 0) := x"DEADBEEF";

end package;
