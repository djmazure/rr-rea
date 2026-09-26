-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- Sim-only unisim.vcomponents stand-in declaring the BSCANE2 component
-- (REA-P3.6), compiled into library `unisim` so rr_rea_jtag_xilinx7's
-- `use unisim.vcomponents.BSCANE2` resolves. The entity that binds to it is
-- fixtures/bscane2_mock.vhd, compiled into `work`: nvc default-binds a
-- component from the working library, and with the entity only in `unisim`
-- the instance was left silently UNBOUND (every BSCANE2 output read 'U').
--
library ieee;
    use ieee.std_logic_1164.all;

package vcomponents is
    component BSCANE2 is
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
    end component;
end package;
