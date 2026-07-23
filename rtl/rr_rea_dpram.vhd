-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_dpram — true dual-port BRAM for the REA capture_i buffer.
--
-- Port A (sample_clk_i domain): synchronous write from the capture_i FSM.
-- Port B (jtag_clk_i domain): synchronous read for the JTAG burst slave.
-- Both ports also expose a synchronous read on their own clock so
-- the capture_i FSM can self-inspect (used by tests).
--
-- The intent is BRAM inference on both Xilinx (BRAM18/36) and Intel
-- (M9K/M20K). Only port A writes and port B is read-only, so this is a
-- SIMPLE dual-port RAM: a single-driver `signal` array (written on
-- clk_a_i, read on clk_b_i) is the canonical SDP inference form both Vivado
-- and Quartus recognise — and, unlike a non-protected `shared variable`
-- (VHDL-2008 §4.2.1), it lints clean under strict GHDL. A shared
-- variable is only needed for TRUE dual-port (two writers), which this
-- is not (RTL-P2.888).
--
-- Contracts (see requirements.yml):
--   REA-REQ-200: write A, read B at same addr → din_a_i appears on
--                dout_b_o on the next clk_b_i edge.
--   REA-REQ-201: write A and read B at DIFFERENT addrs in the same
--                cycle → port B sees the previously-written data.
--   REA-REQ-202: WIDTH and DEPTH generics drive the actual storage
--                shape (round-trip at addr=DEPTH-1, din_i=2^WIDTH-1).

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_dpram is
    generic (
        G_WIDTH : positive := 12;
        G_DEPTH : positive := 4096
    );
    port (
        -- Port A (writer)
        clk_a_i   : in  std_logic;
        we_a_i    : in  std_logic;
        addr_a_i  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        din_a_i   : in  std_logic_vector(G_WIDTH - 1 downto 0);
        dout_a_o  : out std_logic_vector(G_WIDTH - 1 downto 0);
        -- Port B (reader)
        clk_b_i   : in  std_logic;
        addr_b_i  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dout_b_o  : out std_logic_vector(G_WIDTH - 1 downto 0)
    );
end entity;

architecture rtl of rr_rea_dpram is
    type ram_t is array (0 to G_DEPTH - 1)
        of std_logic_vector(G_WIDTH - 1 downto 0);
    signal mem : ram_t := (others => (others => '0'));
begin

    -- Port A: synchronous write (sole driver of `mem`) + synchronous
    -- read on clk_a_i. dout_a_o is `open` in rr_rea_top; it exists only for
    -- the dpram self-inspection unit tests.
    process (clk_a_i)
    begin
        if rising_edge(clk_a_i) then
            if we_a_i = '1' then
                mem(to_integer(unsigned(addr_a_i))) <= din_a_i;
            end if;
            dout_a_o <= mem(to_integer(unsigned(addr_a_i)));
        end if;
    end process;

    -- Port B: synchronous read on clk_b_i (read-only port)
    process (clk_b_i)
    begin
        if rising_edge(clk_b_i) then
            dout_b_o <= mem(to_integer(unsigned(addr_b_i)));
        end if;
    end process;

end architecture;
