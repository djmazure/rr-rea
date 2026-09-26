-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Sim-only mock of Intel's sld_virtual_jtag megafunction (REA-P3.6).
-- rr_rea_intel declares the component locally, so this entity in `work`
-- binds by default. Outputs come from rea_tap_mock_pkg; TDO goes back into
-- it. Port names are Intel's own.

library ieee;
    use ieee.std_logic_1164.all;
library rea_tap_mock;
    use rea_tap_mock.rea_tap_mock_pkg.all;

entity sld_virtual_jtag is
    generic (
        sld_auto_instance_index : string  := "NO";
        sld_instance_index      : integer := 0;
        sld_ir_width            : integer := 1
    );
    port (
        tck               : out std_logic;
        tdi               : out std_logic;
        tdo               : in  std_logic;
        virtual_state_cdr : out std_logic;
        virtual_state_sdr : out std_logic;
        virtual_state_udr : out std_logic;
        ir_in             : out std_logic_vector(sld_ir_width - 1 downto 0);
        ir_out            : in  std_logic_vector(sld_ir_width - 1 downto 0)
    );
end entity;

architecture sim of sld_virtual_jtag is
begin
    tck               <= tap_tck;
    tdi               <= tap_tdi;
    virtual_state_cdr <= tap_capture;
    virtual_state_sdr <= tap_shift;
    virtual_state_udr <= tap_update;
    ir_in             <= (others => '0');
    tap_tdo           <= tdo;
end architecture;
