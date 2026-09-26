-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_wrapper_harness — puts ONE shipped vendor wrapper (rr_rea_xilinx7 or
-- rr_rea_intel, chosen by G_VENDOR) under test with a mocked TAP (REA-P3.6).
-- Its ports carry rr_rea_top's TAP names, so the register-scan helpers of the
-- rr_rea_top tests drive it unchanged; the scan then crosses the mock
-- primitive and the wrapper, which is what proves a wrapper generic or port
-- actually reaches the core.

library ieee;
    use ieee.std_logic_1164.all;
library rea_tap_mock;
    use rea_tap_mock.rea_tap_mock_pkg.all;

entity rr_rea_wrapper_harness is
    generic (
        G_VENDOR      : string   := "xilinx7";
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 64;
        G_TIMESTAMP_W : natural  := 32;
        G_TRIG_CONDS  : positive := 4;
        G_QUAL_CONDS  : natural  := 0;
        G_TRIG_STAGES : natural  := 0
    );
    port (
        sample_clk_i  : in  std_logic;
        sample_rst_i  : in  std_logic;
        probe_i       : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        ext_trigger_i : in  std_logic;
        trigger_o     : out std_logic;
        source_o      : out std_logic_vector(0 downto 0);
        -- mocked TAP, named as on rr_rea_top
        arst_i        : in  std_logic;
        tck_i         : in  std_logic;
        tdi_i         : in  std_logic;
        tdo_o         : out std_logic;
        capture_i     : in  std_logic;
        shift_en_i    : in  std_logic;
        update_i      : in  std_logic;
        sel_i         : in  std_logic
    );
end entity;

architecture sim of rr_rea_wrapper_harness is
begin
    tap_tck     <= tck_i;
    tap_tdi     <= tdi_i;
    tap_capture <= capture_i;
    tap_shift   <= shift_en_i;
    tap_update  <= update_i;
    tap_sel     <= sel_i;
    tdo_o       <= tap_tdo;

    g_xilinx7 : if G_VENDOR = "xilinx7" generate
        u_dut : entity work.rr_rea_xilinx7
            generic map (
                G_SAMPLE_W    => G_SAMPLE_W,
                G_DEPTH       => G_DEPTH,
                G_TIMESTAMP_W => G_TIMESTAMP_W,
                G_QUAL_CONDS  => G_QUAL_CONDS,
                G_TRIG_CONDS  => G_TRIG_CONDS,
                G_TRIG_STAGES => G_TRIG_STAGES
            )
            port map (
                sample_clk_i  => sample_clk_i,
                sample_rst_i  => sample_rst_i,
                probe_i       => probe_i,
                ext_trigger_i => ext_trigger_i,
                source_o      => source_o,
                trigger_o     => trigger_o
            );
    end generate;

    g_intel : if G_VENDOR = "intel" generate
        u_dut : entity work.rr_rea_intel
            generic map (
                G_SAMPLE_W    => G_SAMPLE_W,
                G_DEPTH       => G_DEPTH,
                G_TIMESTAMP_W => G_TIMESTAMP_W,
                G_QUAL_CONDS  => G_QUAL_CONDS,
                G_TRIG_CONDS  => G_TRIG_CONDS,
                G_TRIG_STAGES => G_TRIG_STAGES
            )
            port map (
                sample_clk_i  => sample_clk_i,
                sample_rst_i  => sample_rst_i,
                probe_i       => probe_i,
                ext_trigger_i => ext_trigger_i,
                source_o      => source_o,
                trigger_o     => trigger_o
            );
    end generate;
end architecture;
