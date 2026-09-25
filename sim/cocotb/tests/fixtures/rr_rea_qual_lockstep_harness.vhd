-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- REA-P2.7 storage-qualification harness (REA-REQ-950..955, 959).
--
-- Four capture FSMs on ONE set of stimulus inputs:
--   u_ref  : the frozen pre-qualification FSM (fixtures/..._ref_v09.vhd)
--   u_refd : the same frozen FSM behind ONE register on its sample-side
--            inputs (probe, arm, soft reset, trigger) — REA-P2.11: a build
--            with a qualifier elaborated stores rea_store_lag = 1 cycle late
--   u_zero : the current FSM, G_QUAL_CONDS = 0 (no qualifier elaborated)
--   u_off  : the current FSM, G_QUAL_CONDS = G_QUAL_CONDS, qualifier slots
--            driven with live junk but qual_enable tied '0'
--   u_qual : the current FSM, G_QUAL_CONDS = G_QUAL_CONDS, qualifier driven
--            from the qual_*_i ports below; its outputs keep the FSM's names
-- The lockstep test asserts u_zero matches u_ref and u_off matches u_refd on
-- every output on every cycle; the qualification tests read u_qual.

library ieee;
    use ieee.std_logic_1164.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_qual_lockstep_harness is
    generic (
        G_SAMPLE_W   : positive := 16;
        G_DEPTH      : positive := 64;
        G_QUAL_CONDS : positive := 2
    );
    port (
        sample_clk_i   : in  std_logic;
        sample_rst_i   : in  std_logic;
        probe_i        : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        arm_pulse_i    : in  std_logic;
        reset_pulse_i  : in  std_logic;
        trigger_i      : in  std_logic;
        pretrig_len_i  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_i : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_i   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_i    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mode_i    : in  std_logic_vector(7 downto 0);
        decim_ratio_i  : in  std_logic_vector(23 downto 0);

        -- Qualifier config. u_off sees these slots but qual_enable '0';
        -- u_qual sees all of it.
        qual_enable_i  : in  std_logic;
        qual_or_i      : in  std_logic;
        qual_values_i  : in  std_logic_vector(G_QUAL_CONDS * G_SAMPLE_W - 1 downto 0);
        qual_masks_i   : in  std_logic_vector(G_QUAL_CONDS * G_SAMPLE_W - 1 downto 0);
        qual_ops_i     : in  std_logic_vector(G_QUAL_CONDS * 4 - 1 downto 0);
        qual_valid_i   : in  std_logic_vector(G_QUAL_CONDS - 1 downto 0);

        -- u_ref
        ref_we_o         : out std_logic;
        ref_addr_o       : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        ref_din_o        : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        ref_wr_ptr_o     : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        ref_trig_ptr_o   : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        ref_start_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        ref_pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0);
        ref_status_o     : out std_logic_vector(4 downto 0);
        -- u_refd
        refd_we_o         : out std_logic;
        refd_addr_o       : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        refd_din_o        : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        refd_wr_ptr_o     : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        refd_trig_ptr_o   : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        refd_start_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        refd_pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0);
        refd_status_o     : out std_logic_vector(4 downto 0);
        -- u_zero
        zero_we_o        : out std_logic;
        zero_addr_o      : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        zero_din_o       : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        zero_wr_ptr_o    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        zero_trig_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        zero_start_ptr_o : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        zero_pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0);
        zero_status_o    : out std_logic_vector(4 downto 0);
        -- u_off
        off_we_o         : out std_logic;
        off_addr_o       : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        off_din_o        : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        off_wr_ptr_o     : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        off_trig_ptr_o   : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        off_start_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        off_pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0);
        off_status_o     : out std_logic_vector(4 downto 0);
        -- u_qual: carries the capture FSM's own port names, so a test that
        -- reads dpram_we_o reads the FSM's dpram_we_o (REQ-951..959).
        dpram_we_o        : out std_logic;
        dpram_addr_o      : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dpram_din_o       : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        wr_ptr_o    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        start_ptr_o : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0);
        status_o    : out std_logic_vector(4 downto 0)
    );
end entity;

architecture rtl of rr_rea_qual_lockstep_harness is
    -- status = {trigger_out, overflow, done, triggered, armed}
    signal probe_d   : std_logic_vector(G_SAMPLE_W - 1 downto 0) :=
        (others => '0');
    signal arm_d     : std_logic := '0';
    signal reset_d   : std_logic := '0';
    signal trigger_d : std_logic := '0';
begin

    -- The delay u_refd sees: controls reset with the FSM, data does not.
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            arm_d     <= '0';
            reset_d   <= '0';
            trigger_d <= '0';
        elsif rising_edge(sample_clk_i) then
            arm_d     <= arm_pulse_i;
            reset_d   <= reset_pulse_i;
            trigger_d <= trigger_i;
        end if;
    end process;

    process (sample_clk_i)
    begin
        if rising_edge(sample_clk_i) then
            probe_d <= probe_i;
        end if;
    end process;

    u_refd : entity work.rr_rea_capture_fsm_ref_v09
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk_i => sample_clk_i, sample_rst_i => sample_rst_i,
            probe_i => probe_d, arm_pulse_i => arm_d,
            reset_pulse_i => reset_d, trigger_i => trigger_d,
            pretrig_len_i => pretrig_len_i, posttrig_len_i => posttrig_len_i,
            trig_value_i => trig_value_i, trig_mask_i => trig_mask_i,
            trig_mode_i => trig_mode_i, decim_ratio_i => decim_ratio_i,
            armed_o => refd_status_o(0), triggered_o => refd_status_o(1),
            done_o => refd_status_o(2), overflow_o => refd_status_o(3),
            trigger_o => refd_status_o(4),
            dpram_we_o => refd_we_o, dpram_addr_o => refd_addr_o,
            dpram_din_o => refd_din_o, wr_ptr_o => refd_wr_ptr_o,
            trig_ptr_o => refd_trig_ptr_o, start_ptr_o => refd_start_ptr_o,
            pretrig_valid_o => refd_pretrig_valid_o);

    u_ref : entity work.rr_rea_capture_fsm_ref_v09
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk_i => sample_clk_i, sample_rst_i => sample_rst_i,
            probe_i => probe_i, arm_pulse_i => arm_pulse_i,
            reset_pulse_i => reset_pulse_i, trigger_i => trigger_i,
            pretrig_len_i => pretrig_len_i, posttrig_len_i => posttrig_len_i,
            trig_value_i => trig_value_i, trig_mask_i => trig_mask_i,
            trig_mode_i => trig_mode_i, decim_ratio_i => decim_ratio_i,
            armed_o => ref_status_o(0), triggered_o => ref_status_o(1),
            done_o => ref_status_o(2), overflow_o => ref_status_o(3),
            trigger_o => ref_status_o(4),
            dpram_we_o => ref_we_o, dpram_addr_o => ref_addr_o,
            dpram_din_o => ref_din_o, wr_ptr_o => ref_wr_ptr_o,
            trig_ptr_o => ref_trig_ptr_o, start_ptr_o => ref_start_ptr_o,
            pretrig_valid_o => ref_pretrig_valid_o);

    u_zero : entity work.rr_rea_capture_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH,
                     G_QUAL_CONDS => 0)
        port map (
            sample_clk_i => sample_clk_i, sample_rst_i => sample_rst_i,
            probe_i => probe_i, arm_pulse_i => arm_pulse_i,
            reset_pulse_i => reset_pulse_i, trigger_i => trigger_i,
            pretrig_len_i => pretrig_len_i, posttrig_len_i => posttrig_len_i,
            trig_value_i => trig_value_i, trig_mask_i => trig_mask_i,
            trig_mode_i => trig_mode_i, decim_ratio_i => decim_ratio_i,
            armed_o => zero_status_o(0), triggered_o => zero_status_o(1),
            done_o => zero_status_o(2), overflow_o => zero_status_o(3),
            trigger_o => zero_status_o(4),
            dpram_we_o => zero_we_o, dpram_addr_o => zero_addr_o,
            dpram_din_o => zero_din_o, wr_ptr_o => zero_wr_ptr_o,
            trig_ptr_o => zero_trig_ptr_o, start_ptr_o => zero_start_ptr_o,
            pretrig_valid_o => zero_pretrig_valid_o);

    u_off : entity work.rr_rea_capture_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH,
                     G_QUAL_CONDS => G_QUAL_CONDS)
        port map (
            sample_clk_i => sample_clk_i, sample_rst_i => sample_rst_i,
            probe_i => probe_i, arm_pulse_i => arm_pulse_i,
            reset_pulse_i => reset_pulse_i, trigger_i => trigger_i,
            pretrig_len_i => pretrig_len_i, posttrig_len_i => posttrig_len_i,
            trig_value_i => trig_value_i, trig_mask_i => trig_mask_i,
            trig_mode_i => trig_mode_i, decim_ratio_i => decim_ratio_i,
            qual_enable_i => '0', qual_or_i => qual_or_i,
            qual_values_i => qual_values_i, qual_masks_i => qual_masks_i,
            qual_ops_i => qual_ops_i, qual_valid_i => qual_valid_i,
            armed_o => off_status_o(0), triggered_o => off_status_o(1),
            done_o => off_status_o(2), overflow_o => off_status_o(3),
            trigger_o => off_status_o(4),
            dpram_we_o => off_we_o, dpram_addr_o => off_addr_o,
            dpram_din_o => off_din_o, wr_ptr_o => off_wr_ptr_o,
            trig_ptr_o => off_trig_ptr_o, start_ptr_o => off_start_ptr_o,
            pretrig_valid_o => off_pretrig_valid_o);

    u_qual : entity work.rr_rea_capture_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH,
                     G_QUAL_CONDS => G_QUAL_CONDS)
        port map (
            sample_clk_i => sample_clk_i, sample_rst_i => sample_rst_i,
            probe_i => probe_i, arm_pulse_i => arm_pulse_i,
            reset_pulse_i => reset_pulse_i, trigger_i => trigger_i,
            pretrig_len_i => pretrig_len_i, posttrig_len_i => posttrig_len_i,
            trig_value_i => trig_value_i, trig_mask_i => trig_mask_i,
            trig_mode_i => trig_mode_i, decim_ratio_i => decim_ratio_i,
            qual_enable_i => qual_enable_i, qual_or_i => qual_or_i,
            qual_values_i => qual_values_i, qual_masks_i => qual_masks_i,
            qual_ops_i => qual_ops_i, qual_valid_i => qual_valid_i,
            armed_o => status_o(0), triggered_o => status_o(1),
            done_o => status_o(2), overflow_o => status_o(3),
            trigger_o => status_o(4),
            dpram_we_o => dpram_we_o, dpram_addr_o => dpram_addr_o,
            dpram_din_o => dpram_din_o, wr_ptr_o => wr_ptr_o,
            trig_ptr_o => trig_ptr_o, start_ptr_o => start_ptr_o,
            pretrig_valid_o => pretrig_valid_o);

end architecture;
