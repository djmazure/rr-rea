-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Sim-only mock of the Xilinx BSCANE2 primitive (REA-P3.6), compiled into
-- `work` (see fixtures/unisim_vcomponents_mock.vhd for why). Outputs come
-- from rea_tap_mock_pkg; TDO goes back into it. Port names are Xilinx's own
-- (the ESA suffixes stop at the vendor boundary).

library ieee;
    use ieee.std_logic_1164.all;
library rea_tap_mock;
    use rea_tap_mock.rea_tap_mock_pkg.all;

entity BSCANE2 is
    generic (JTAG_CHAIN : integer := 1);
    port (
        CAPTURE : out std_logic;
        DRCK    : out std_logic;
        RESET   : out std_logic;
        RUNTEST : out std_logic;
        SEL     : out std_logic;
        SHIFT   : out std_logic;
        TCK     : out std_logic;
        TDI     : out std_logic;
        TMS     : out std_logic;
        UPDATE  : out std_logic;
        TDO     : in  std_logic
    );
end entity;

architecture sim of BSCANE2 is
begin
    CAPTURE <= tap_capture;
    DRCK    <= tap_tck;
    RESET   <= '0';
    RUNTEST <= '0';
    SEL     <= tap_sel;
    SHIFT   <= tap_shift;
    TCK     <= tap_tck;
    TDI     <= tap_tdi;
    TMS     <= '0';
    UPDATE  <= tap_update;
    tap_tdo <= TDO;
end architecture;
