-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_trust_core — v0.8 sample-domain trust core (REA-P3.2 extraction).
--
-- The v0.8 "trust tier" readback-integrity logic (CRC sweep + sample-plane CRC
-- latch + snapshot/settle publication FSM + port-A single-owner arbiter +
-- CAPTURE_EPOCH counter + invalidate toggle + the selftest fill FSM) is
-- extracted VERBATIM out of rr_rea_top so its control invariants are FORMALLY
-- PROVABLE. On the full top every one of these invariants is conditioned on
-- arm_pulse / reset_pulse / capture-done / fill signals that sit ~40-60 cycles
-- behind the BSCAN shift/update protocol, so any feasible-depth proof there is
-- VACUOUS (the antecedents are never exercised — REA-P3.2). Here those control
-- signals are FREE INPUT PORTS, so the antecedents are reachable in a couple of
-- cycles and the invariants prove directly (k-induction), exactly like the
-- capture FSM's REQ-100..107 and the fill FSM's REA-T1.2 guard do on their own
-- free ports.
--
-- Behaviour is byte-identical to the in-top processes it replaces; the top now
-- just instantiates this core, keeps the observation signals it needs as ports,
-- and hangs the JTAG-domain CDC + reg-domain crc_valid endpoint + the (mirror)
-- timestamp plane off the exposed sample-domain control (sweep_start_o,
-- sweep_rst_o, selftest_mode_o) and status (ts_done_i) ports.
--
-- ASSUME-GUARANTEE boundary (see verif/rr_rea_trust_core_formal.psl):
--   The capture writes (cap_we_i / cap_addr_i / cap_din_i) and the capture-done
--   level (done_i) are FREE inputs, constrained in the formal harness by the
--   rr_rea_capture_fsm's OWN proven contract (REQ-100..107):
--     * a capture write only happens before done   (cap_we_i -> not done_i),
--     * done is sticky until an arm/soft-reset      (no fresh 0->1 done edge
--       without an intervening arm_pulse/reset_pulse).
--   Those guarantees are proven separately on rr_rea_capture_fsm's free ports;
--   assuming them here is a sound assume-guarantee decomposition, not a gap.
--
-- Contract (see requirements.yml):
--   REQ-800 : publication can only be pending once the sweep engine is idle.
--   REQ-803 : the invalidate toggle flips ONLY on an arm / soft-reset pulse.
--   REQ-807 : CAPTURE_EPOCH bumps on exactly arm / soft-reset / accepted fill.
--   REQ-808 : snapshot-settle-toggle publication; a mid-flight epoch change
--             (arm / fill / reset) cancels the publish (invalidation wins).
--   REQ-810 : port-A single-owner — fill / capture / sweep are mutually
--             exclusive under fixed priority fill > sweep > capture.
--   REQ-811 : a capture-domain port-A write implies this generation has not yet
--             published (pub_done_r = '0').
--   REQ-850..854 : delegated to the instantiated rr_rea_fill_fsm.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;
library work;
    use work.rr_rea_pkg.all;

entity rr_rea_trust_core is
    generic (
        G_SAMPLE_W   : positive := 12;
        G_DEPTH      : positive := 4096;
        G_PUB_SETTLE : natural  := 8      -- publication settle window (cycles)
    );
    port (
        sample_clk_i    : in  std_logic;
        sample_rst_i    : in  std_logic;
        -- Control stimuli (sample_clk_i domain; FREE inputs for formal). On the
        -- full top these are the CDC'd JTAG pulses / capture-FSM outputs.
        arm_pulse_i     : in  std_logic;
        reset_pulse_i   : in  std_logic;
        armed_i         : in  std_logic;   -- capture FSM: armed (fill refuse)
        triggered_i     : in  std_logic;   -- capture FSM: triggered (fill refuse)
        done_i          : in  std_logic;   -- capture FSM: done level
        -- Capture-domain port-A write (from rr_rea_capture_fsm).
        cap_we_i        : in  std_logic;
        cap_addr_i      : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        cap_din_i       : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- Selftest fill request / seed (from the JTAG regbank via CDC).
        fill_request_i  : in  std_logic;
        selftest_seed_i : in  std_logic_vector(31 downto 0);
        -- Port-A readback data (DPRAM dout_a → the sweep CRC engine).
        sweep_mem_dout_i : in std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- Timestamp-plane sweep completion (its own sweep mirrors this core's
        -- sweep_start_o / sweep_rst_o; driven '1' when there is no ts plane).
        ts_done_i       : in  std_logic;
        -- ── Arbitrated port-A (to the sample DPRAM) ──────────────────
        dpram_we_a_o    : out std_logic;
        dpram_addr_a_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dpram_din_a_o   : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- ── Sweep control exposed for the timestamp-plane mirror ─────
        sweep_start_o   : out std_logic;
        sweep_rst_o     : out std_logic;
        sweep_busy_o    : out std_logic;
        sweep_owns_a_o  : out std_logic;
        -- ── Trust status / CRC / epoch / publication ─────────────────
        crc_sample_o    : out std_logic_vector(31 downto 0);
        sample_done_o   : out std_logic;
        capture_epoch_o : out std_logic_vector(31 downto 0);
        publish_toggle_o    : out std_logic;
        invalidate_toggle_o : out std_logic;
        pub_pending_o   : out std_logic;
        pub_done_o      : out std_logic;
        -- ── Fill FSM pass-through (for CDC + selftest status in the top) ──
        fill_we_o          : out std_logic;
        fill_busy_o        : out std_logic;
        fill_done_o        : out std_logic;
        fill_accept_o      : out std_logic;
        selftest_mode_o    : out std_logic;
        selftest_refused_o : out std_logic
    );
end entity;

architecture rtl of rr_rea_trust_core is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    -- Epoch / invalidate.
    signal capture_epoch_r    : std_logic_vector(31 downto 0) := (others => '0');
    signal invalidate_toggle_r : std_logic := '0';

    -- Sweep.
    signal sweep_rst        : std_logic;
    signal sweep_start      : std_logic;
    signal prev_done_r      : std_logic := '0';
    signal sweep_owns_a     : std_logic;
    signal sweep_busy       : std_logic;
    signal sweep_crc_done   : std_logic;
    signal sweep_crc_o      : std_logic_vector(31 downto 0);
    signal sweep_mem_addr   : std_logic_vector(C_PTR_W - 1 downto 0);

    -- Fill FSM.
    signal sweep_active   : std_logic;  -- sweep busy OR about to start (REA-T1.4)
    signal fill_addr_slv  : std_logic_vector(C_PTR_W - 1 downto 0);
    signal fill_we        : std_logic;
    signal fill_din       : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal fill_busy      : std_logic;
    signal fill_done      : std_logic;
    signal fill_accept    : std_logic;
    signal selftest_mode_r    : std_logic;
    signal selftest_refused_r : std_logic;

    -- Sample-plane CRC latch.
    signal crc_sample_r    : std_logic_vector(31 downto 0) := (others => '0');
    signal sample_done_r   : std_logic := '0';

    -- Publication FSM.
    signal pub_done_r       : std_logic := '0';
    signal epoch_snap_r     : std_logic_vector(31 downto 0) := (others => '0');
    signal pub_settle_r     : natural range 0 to G_PUB_SETTLE := 0;
    signal pub_pending_r    : std_logic := '0';
    signal publish_toggle_r : std_logic := '0';

begin

    -- ── CAPTURE_EPOCH counter (REQ-807) ──────────────────────────────
    -- Bumps on exactly: accepted arm, soft reset, accepted selftest fill.
    -- sample_rst_i is the hard reset to 0. Nothing else moves it.
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            capture_epoch_r <= (others => '0');
        elsif rising_edge(sample_clk_i) then
            if arm_pulse_i = '1' or reset_pulse_i = '1'
               or fill_accept = '1' then     -- REQ-853: accepted fill bumps epoch
                capture_epoch_r <= std_logic_vector(unsigned(capture_epoch_r) + 1);
            end if;
        end if;
    end process;

    -- ── CRC sweep ─────────────────────────────────────────────────────
    -- Abort on arm / soft reset so capture reclaims port A (REQ-803). The
    -- pulses are registered, so this is a synchronous reset assertion.
    sweep_rst <= sample_rst_i or arm_pulse_i or reset_pulse_i;

    -- Start a sweep on the rising edge of done (capture just completed), or when
    -- a selftest fill has just finished (so the pattern is validated). REA-P2.3.
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            prev_done_r <= '0';
        elsif rising_edge(sample_clk_i) then
            prev_done_r <= done_i;
        end if;
    end process;
    sweep_start <= (done_i and not prev_done_r) or fill_done;

    -- REA-T1.4: the fill FSM must refuse not only while a sweep is BUSY but also
    -- on the cycle a sweep is about to START (sweep_start). sweep_busy lags
    -- sweep_start by one cycle (crc_sweep goes busy the cycle after start_i), so
    -- a fill accepted on the exact cycle a prior fill's fill_done launches the
    -- validation sweep would win the port-A arbiter and corrupt that sweep's read
    -- (the T1.2 sweep_busy guard alone is one cycle too late). Feeding
    -- sweep_busy OR sweep_start closes the boundary. No comb loop: fill_done
    -- (hence sweep_start) is a registered fill-FSM output.
    sweep_active <= sweep_busy or sweep_start;

    -- Port-A arbiter, fixed priority fill > sweep > capture (reset/arm handled
    -- upstream via sweep_rst / the fill FSM's refuse-while-armed). The fill and
    -- sweep never overlap (fill completes, THEN triggers the sweep), and the
    -- fill only runs while capture is quiesced (selftest_mode), so this reduces
    -- to a clean single-owner mux.
    sweep_owns_a <= sweep_busy;
    dpram_addr_a_o <= fill_addr_slv when fill_busy = '1'
                      else sweep_mem_addr           when sweep_owns_a = '1'
                      else cap_addr_i;
    -- Capture writes are suppressed for the whole selftest (selftest_mode) so
    -- the fill pattern is not overwritten by the free-running capture after the
    -- fill completes — the buffer stays frozen until the next arm.
    dpram_we_a_o   <= fill_we when fill_busy = '1'
                      else '0' when (sweep_owns_a = '1' or selftest_mode_r = '1')
                      else cap_we_i;
    dpram_din_a_o  <= fill_din when fill_busy = '1' else cap_din_i;

    -- ── Fill FSM (REA-P3.2 extraction, REQ-850..854) ─────────────────
    u_fill_fsm : entity work.rr_rea_fill_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk_i       => sample_clk_i,
            sample_rst_i       => sample_rst_i,
            arm_pulse_i        => arm_pulse_i,
            reset_pulse_i      => reset_pulse_i,
            fill_request_i     => fill_request_i,
            armed_i            => armed_i,
            triggered_i        => triggered_i,
            sweep_busy_i       => sweep_active,   -- REA-T1.4: busy OR starting
            selftest_seed_i    => selftest_seed_i,
            fill_we_o          => fill_we,
            fill_busy_o        => fill_busy,
            fill_din_o         => fill_din,
            fill_addr_o        => fill_addr_slv,
            fill_done_o        => fill_done,
            fill_accept_o      => fill_accept,
            selftest_mode_o    => selftest_mode_r,
            selftest_refused_o => selftest_refused_r
        );

    u_crc_sweep : entity work.rr_rea_crc_sweep
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk_i => sample_clk_i,
            sample_rst_i => sweep_rst,
            start_i      => sweep_start,
            mem_dout_i   => sweep_mem_dout_i,
            mem_addr_o   => sweep_mem_addr,
            mem_rd_en_o  => open,
            busy_o       => sweep_busy,
            crc_done_o   => sweep_crc_done,
            crc_o        => sweep_crc_o
        );

    -- Sample-plane CRC latch + done flag (REQ-800). The timestamp plane drives
    -- its own crc_ts_r / ts_done_r (fed back via ts_done_i, or ts_done_i='1'
    -- when there is no timestamp plane, so publication gates on this plane only).
    process (sample_clk_i, sweep_rst)
    begin
        if sweep_rst = '1' then
            crc_sample_r  <= (others => '0');
            sample_done_r <= '0';
        elsif rising_edge(sample_clk_i) then
            if sweep_crc_done = '1' then
                crc_sample_r  <= sweep_crc_o;
                sample_done_r <= '1';
            end if;
        end if;
    end process;

    -- Publication FSM (REQ-808). Once BOTH plane sweeps have completed, snapshot
    -- the epoch and hold a settle window (so the crc_sample / crc_ts word syncs
    -- fully land in the jtag domain) before flipping publish_toggle_r — only
    -- then can crc_valid rise. pub_done_r fires exactly one publish per capture
    -- generation; a mid-settle epoch change cancels; sweep_rst (arm/reset)
    -- resets the whole FSM.
    process (sample_clk_i, sweep_rst)
    begin
        if sweep_rst = '1' then
            pub_pending_r <= '0';
            pub_done_r    <= '0';
            pub_settle_r  <= 0;
        elsif rising_edge(sample_clk_i) then
            if sample_done_r = '1' and ts_done_i = '1'
               and pub_pending_r = '0' and pub_done_r = '0' then
                epoch_snap_r  <= capture_epoch_r;
                pub_settle_r  <= G_PUB_SETTLE;
                pub_pending_r <= '1';
            elsif pub_pending_r = '1' then
                if epoch_snap_r /= capture_epoch_r then
                    pub_pending_r <= '0';                     -- invalidated: cancel
                    pub_done_r    <= '1';
                elsif pub_settle_r = 0 then
                    publish_toggle_r <= not publish_toggle_r; -- publish
                    pub_pending_r <= '0';
                    pub_done_r    <= '1';
                else
                    pub_settle_r <= pub_settle_r - 1;
                end if;
            end if;
        end if;
    end process;

    -- Invalidate toggle: flips on any epoch bump from arm / soft reset (a fill's
    -- epoch bump does NOT flip it — a fill freezes rather than mutates the buffer
    -- and does not clear a prior crc_valid; REQ-811 fill branch, see REA-T2.1).
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            invalidate_toggle_r <= '0';
        elsif rising_edge(sample_clk_i) then
            if arm_pulse_i = '1' or reset_pulse_i = '1' then
                invalidate_toggle_r <= not invalidate_toggle_r;
            end if;
        end if;
    end process;

    -- ── Outputs ──────────────────────────────────────────────────────
    sweep_start_o       <= sweep_start;
    sweep_rst_o         <= sweep_rst;
    sweep_busy_o        <= sweep_busy;
    sweep_owns_a_o      <= sweep_owns_a;
    crc_sample_o        <= crc_sample_r;
    sample_done_o       <= sample_done_r;
    capture_epoch_o     <= capture_epoch_r;
    publish_toggle_o    <= publish_toggle_r;
    invalidate_toggle_o <= invalidate_toggle_r;
    pub_pending_o       <= pub_pending_r;
    pub_done_o          <= pub_done_r;
    fill_we_o           <= fill_we;
    fill_busy_o         <= fill_busy;
    fill_done_o         <= fill_done;
    fill_accept_o       <= fill_accept;
    selftest_mode_o     <= selftest_mode_r;
    selftest_refused_o  <= selftest_refused_r;

end architecture;
