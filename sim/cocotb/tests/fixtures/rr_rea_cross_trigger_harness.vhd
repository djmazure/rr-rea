-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_cross_trigger_harness — SIM-ONLY fixture for the cross-core trigger
-- (REA-P2.29, REA-REQ-975/976, REA-REQ-400..402).
--
-- TWO rr_rea_top cores on two unrelated sample clocks, coupled exactly as
-- SPEC "Cross-core trigger" tells an integrator to couple them:
--
--   u_a.trigger_o -> u_xbar.toggle_a_i   u_xbar.pulse_b_o -> u_b.trigger_i
--   u_b.trigger_o -> u_xbar.toggle_b_i   u_xbar.pulse_a_o -> u_a.trigger_i
--
-- Each core has its own register door (its own TAP sel/capture/shift/update/
-- tdi/tdo; tck and the TAP reset are shared), so the test configures and
-- reads back each core independently. The xbar pulses are brought out so the
-- test can see what reached each core's trigger_i.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_cross_trigger_harness is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 256;
        G_TIMESTAMP_W : natural  := 0
    );
    port (
        -- ── Core A sample domain ─────────────────────────────────
        clk_a_i           : in  std_logic;
        rst_a_i           : in  std_logic;
        probe_a_i         : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        a_trigger_o       : out std_logic;
        a_trigger_pulse_o : out std_logic;
        xbar_pulse_a_o    : out std_logic;   -- what reaches u_a.trigger_i

        -- ── Core B sample domain ─────────────────────────────────
        clk_b_i           : in  std_logic;
        rst_b_i           : in  std_logic;
        probe_b_i         : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        b_trigger_o       : out std_logic;
        b_trigger_pulse_o : out std_logic;
        xbar_pulse_b_o    : out std_logic;   -- what reaches u_b.trigger_i

        -- ── Shared TAP clock and reset ───────────────────────────
        arst_i            : in  std_logic;
        tck_i             : in  std_logic;

        -- ── Core A TAP ───────────────────────────────────────────
        a_tdi_i           : in  std_logic;
        a_tdo_o           : out std_logic;
        a_capture_i       : in  std_logic;
        a_shift_en_i      : in  std_logic;
        a_update_i        : in  std_logic;
        a_sel_i           : in  std_logic;

        -- ── Core B TAP ───────────────────────────────────────────
        b_tdi_i           : in  std_logic;
        b_tdo_o           : out std_logic;
        b_capture_i       : in  std_logic;
        b_shift_en_i      : in  std_logic;
        b_update_i        : in  std_logic;
        b_sel_i           : in  std_logic
    );
end entity;

architecture tb of rr_rea_cross_trigger_harness is

    signal toggle_a : std_logic;
    signal toggle_b : std_logic;
    signal pulse_a  : std_logic;
    signal pulse_b  : std_logic;
    signal source_a : std_logic_vector(0 downto 0);
    signal source_b : std_logic_vector(0 downto 0);

begin

    a_trigger_o    <= toggle_a;
    b_trigger_o    <= toggle_b;
    xbar_pulse_a_o <= pulse_a;
    xbar_pulse_b_o <= pulse_b;

    u_a : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W
        )
        port map (
            sample_clk_i    => clk_a_i,
            sample_rst_i    => rst_a_i,
            probe_i         => probe_a_i,
            source_o        => source_a,
            trigger_i       => pulse_a,
            trigger_o       => toggle_a,
            trigger_pulse_o => a_trigger_pulse_o,
            arst_i          => arst_i,
            tck_i           => tck_i,
            tdi_i           => a_tdi_i,
            tdo_o           => a_tdo_o,
            capture_i       => a_capture_i,
            shift_en_i      => a_shift_en_i,
            update_i        => a_update_i,
            sel_i           => a_sel_i
        );

    u_b : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W
        )
        port map (
            sample_clk_i    => clk_b_i,
            sample_rst_i    => rst_b_i,
            probe_i         => probe_b_i,
            source_o        => source_b,
            trigger_i       => pulse_b,
            trigger_o       => toggle_b,
            trigger_pulse_o => b_trigger_pulse_o,
            arst_i          => arst_i,
            tck_i           => tck_i,
            tdi_i           => b_tdi_i,
            tdo_o           => b_tdo_o,
            capture_i       => b_capture_i,
            shift_en_i      => b_shift_en_i,
            update_i        => b_update_i,
            sel_i           => b_sel_i
        );

    u_xbar : entity work.rr_rea_trig_xbar
        port map (
            clk_a_i    => clk_a_i,
            rst_a_i    => rst_a_i,
            toggle_a_i => toggle_a,
            pulse_a_o  => pulse_a,
            clk_b_i    => clk_b_i,
            rst_b_i    => rst_b_i,
            toggle_b_i => toggle_b,
            pulse_b_o  => pulse_b
        );

end architecture;
