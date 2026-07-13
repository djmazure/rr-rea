-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Sim/lint fixture (RTL-T2.119): a 0-valued rr_rea_build_id_pkg for compiling
-- the REA IP STANDALONE (regbank/top unit sims, lint). The IP does NOT ship a
-- committed build-id package — it is a build-flow-GENERATED input (a consuming
-- project regenerates it with a real source hash via the rr_rea_build_id
-- pre_build hook). Shipping a stub in the synthesised source tree collided with
-- the consumer's generated copy (same package name); keeping the standalone
-- placeholder here, OUT of the synth source set, means a consumer build only
-- ever sees ONE rr_rea_build_id_pkg — its own generated one.

library ieee;
    use ieee.std_logic_1164.all;

package rr_rea_build_id_pkg is

    constant C_REA_BUILD_ID : std_logic_vector(31 downto 0) := x"00000000";

end package;
