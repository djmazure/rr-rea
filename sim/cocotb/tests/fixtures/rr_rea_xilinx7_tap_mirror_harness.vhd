-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_xilinx7_tap_mirror_harness — REA-P2.22. Puts rr_rea_xilinx7 over
-- the mocked BSCANE2 (rea_tap_mock_pkg) with its TAP mirror outputs
-- connected, so a test can drive TCK/TMS/TDI and the DR control phases and
-- compare tap_tck/tap_tms/tap_tdi/tap_tdo against what the primitive sees.
-- Port names for the mocked TAP match rr_rea_wrapper_harness so the shared
-- rea_wrapper_generics read/write helpers drive it unchanged.

library ieee;
    use ieee.std_logic_1164.all;

library rea_tap_mock;
    use rea_tap_mock.rea_tap_mock_pkg.all;

entity rr_rea_xilinx7_tap_mirror_harness is
    generic (
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
        tms_i         : in  std_logic;
        tdi_i         : in  std_logic;
        tdo_o         : out std_logic;
        capture_i     : in  std_logic;
        shift_en_i    : in  std_logic;
        update_i      : in  std_logic;
        sel_i         : in  std_logic;
        -- the wrapper's mirror outputs under test
        mon_tck       : out std_logic;
        mon_tms       : out std_logic;
        mon_tdi       : out std_logic;
        mon_tdo       : out std_logic
    );
end entity;

architecture sim of rr_rea_xilinx7_tap_mirror_harness is
begin
    tap_tck     <= tck_i;
    tap_tms     <= tms_i;
    tap_tdi     <= tdi_i;
    tap_capture <= capture_i;
    tap_shift   <= shift_en_i;
    tap_update  <= update_i;
    tap_sel     <= sel_i;
    tdo_o       <= tap_tdo;

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
            trigger_o     => trigger_o,
            tap_tck       => mon_tck,
            tap_tms       => mon_tms,
            tap_tdi       => mon_tdi,
            tap_tdo       => mon_tdo
        );
end architecture;
