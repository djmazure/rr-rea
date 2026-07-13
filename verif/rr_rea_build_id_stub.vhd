-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Publish-LINT stub (RTL-P3.415 / RTL-T2.119): a 0-valued rr_rea_build_id_pkg
-- that lets the strict pre-publish GHDL gate analyse rr_rea_regbank.vhd, which
-- does `use work.rr_rea_build_id_pkg`. The IP does NOT ship a committed build-id
-- package — it is a build-flow-GENERATED input (a consuming project regenerates
-- it with a real source hash via the rr_rea_build_id pre_build hook, declared
-- under build.hooks in ip.yml). This stub is declared as that hook's
-- `lint_stub:` and is LINT-ONLY: it is NEVER placed in the publish bundle or a
-- consumer's synth compile set (shipping a stub there collided with the
-- consumer's generated copy — same package name — the T2.119 stub-collision
-- bug). A consumer build only ever sees ONE rr_rea_build_id_pkg: its own
-- generated one. (The identical standalone sim fixture lives at
-- sim/cocotb/tests/rea/fixtures/rr_rea_build_id_stub.vhd for unit sims.)

library ieee;
    use ieee.std_logic_1164.all;

package rr_rea_build_id_pkg is

    constant C_REA_BUILD_ID : std_logic_vector(31 downto 0) := x"00000000";

end package;
