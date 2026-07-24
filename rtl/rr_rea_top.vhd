-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_top — RouteRTL Embedded Analyzer top-level integration.
--
-- Vendor-neutral: takes JTAG TAP signals as ports rather than
-- instantiating BSCANE2 / sld_virtual_jtag / etc. The Xilinx wrapper
-- (rr_rea_jtag_xilinx7.vhd) is a separate thin shim that connects
-- BSCANE2 to this top. In simulation, the cocotb testbench drives
-- the TAP signals directly — zero vendor primitive needed.
--
-- Wiring:
--   JTAG iface     ◄──► regbank          (reg-bus)
--   regbank        ──── CDCs ────►       capture_fsm config inputs
--   capture_fsm    ──── CDCs ────►       regbank status mirror
--   capture_fsm    ──────────────►       dpram (port A: write)
--   dpram (port B: read) ────────►       JTAG iface (when reg_addr_o in DPRAM window)
--
-- See SPEC.md and requirements.yml REA-REQ-300 for the contract.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_top is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1;
        G_TRIG_CONDS  : positive := 4;  -- v0.5 comparator-array slots (P3.647)
        G_NUM_SOURCE  : positive := 1   -- v0.5 write-side source bits (P2.837)
        -- RTL-T2.119: G_BUILD_ID generic removed; rr_rea_regbank reads the
        -- BUILD_ID (0xD4) source hash from rr_rea_build_id_pkg directly.
    );
    port (
        -- ── Sample-clock domain ──────────────────────────────────
        sample_clk_i : in  std_logic;
        sample_rst_i : in  std_logic;
        probe_i   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Write-side source (RTL-P2.837) ───────────────────────
        -- ISSP-style JTAG-writable control bit(s) driven INTO the design —
        -- the write counterpart to the read-only probe path. The host sets
        -- a bit over JTAG (System Console / xsdb) to release a gated DUT
        -- signal, clears it to re-gate. Presented HERE in the sample_clk_i
        -- domain, having crossed from jtag_clk_i via rr_rea_sync_word (the
        -- proven two-flop word synchronizer, REA-REQ-020/021) exactly like
        -- every other config word — NEVER a bespoke single flop. Powers up
        -- all-zeros (GSR init on the sync flops + regbank reset) so the
        -- gated signal starts SAFE/inactive until explicitly written.
        --
        -- SDC: the jtag_clk_i → sample_clk_i crossing this port rides on is
        -- asynchronous and MUST get the same `set_clock_groups -asynchronous`
        -- treatment as the existing REA config crossings wherever this core
        -- is integrated into a board/example design (see SPEC.md
        -- "Write-side source"). Wire source_o so that a bit = 0 holds the
        -- DUT signal in its safe state (e.g. bist_start <= bist_start_i and
        -- source_o(0)); the reset default then gates by construction.
        source_o : out std_logic_vector(G_NUM_SOURCE - 1 downto 0);

        -- ── External board-pin trigger (RTL-P3.266) ─────────────
        -- Async package-pin input the user routes from a board pin (scope
        -- trigger-out, another FPGA's trigger_o, a button). Synced inside
        -- and folded into the fire decision per TRIG_MODE ext_en[3]/ext_and[8].
        -- Defaults '0' so existing instantiations that don't drive it are
        -- unaffected (internal-only trigger).
        ext_trigger_i : in std_logic := '0';

        -- ── Local trigger pulse (1-cycle on sample_clk_i) ─────────
        -- Exposed so the design can route it to an LED / external
        -- pin / cross-domain trigger crossbar (v0.2). Doubles as a
        -- "Vivado optimizer anchor" — without an observable output
        -- the whole REA hierarchy gets pruned in synthesis.
        trigger_o : out std_logic;

        -- ── JTAG TAP (jtag_clk_i domain) — driven by external wrapper
        --    in synth, driven by testbench in sim ─────────────────
        arst_i       : in  std_logic;
        tck_i        : in  std_logic;
        tdi_i        : in  std_logic;
        tdo_o        : out std_logic;
        capture_i    : in  std_logic;
        shift_en_i   : in  std_logic;
        update_i     : in  std_logic;
        sel_i        : in  std_logic
    );
end entity;

architecture rtl of rr_rea_top is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    -- ── Reg-bus wires (jtag_clk_i domain) ──────────────────────────
    signal reg_clk_o    : std_logic;
    signal reg_rst_o    : std_logic;
    signal reg_wr_en_o  : std_logic;
    signal reg_rd_en_o  : std_logic;
    signal reg_addr_o   : std_logic_vector(15 downto 0);
    signal reg_wdata_o  : std_logic_vector(31 downto 0);
    signal reg_rdata_i  : std_logic_vector(31 downto 0);

    -- ── Regbank → CDC → FSM config (jtag_clk_i → sample_clk_i) ──────
    signal pretrig_jclk  : std_logic_vector(C_PTR_W - 1 downto 0);
    signal posttrig_jclk : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_value_jclk : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal trig_mask_jclk  : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal arm_toggle_jclk   : std_logic;
    signal reset_toggle_jclk : std_logic;
    signal trig_mode_jclk    : std_logic_vector(31 downto 0);
    signal chan_sel_jclk     : std_logic_vector(7 downto 0);
    signal decim_ratio_jclk  : std_logic_vector(23 downto 0);
    signal data_word_sel_jclk : std_logic_vector(7 downto 0);
    signal data_plane_sel_jclk : std_logic;
    signal decim_ratio_sclk  : std_logic_vector(23 downto 0);

    -- ── Write-side source (RTL-P2.837): regbank → CDC → DUT port ──
    signal source_jclk : std_logic_vector(G_NUM_SOURCE - 1 downto 0);
    signal source_sclk : std_logic_vector(G_NUM_SOURCE - 1 downto 0);
    signal probe_sclk  : std_logic_vector(G_SAMPLE_W - 1 downto 0);

    signal pretrig_sclk    : std_logic_vector(C_PTR_W - 1 downto 0);
    signal posttrig_sclk   : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_value_sclk : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal trig_mask_sclk  : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    -- Low 16 bits of TRIG_MODE crossed to sample_clk_i: [7:0] op byte
    -- (P3.644/645) + the enable bits seq[1]/array[2]/ext_en[3], and [8] =
    -- ext_and combine mode (RTL-P3.266). Widened from 8 → 16 to carry bit 8.
    signal trig_mode_sclk  : std_logic_vector(15 downto 0);
    signal ext_trig_sclk   : std_logic_vector(0 downto 0);  -- synced board pin
    signal arm_pulse_sclk   : std_logic;
    signal reset_pulse_sclk : std_logic;

    -- ── v0.8 CAPTURE_EPOCH (REA-P2.2, REQ-807): sample-domain generation
    --    counter, crossed to jtag_clk_i for the regbank. The host's anti-tear
    --    anchor — increments on accepted arm / soft reset (and sweep abort /
    --    accepted fill once P2.2p2-sweep / P2.3 land). ─────────────────────
    signal capture_epoch_r    : std_logic_vector(31 downto 0) := (others => '0');
    signal capture_epoch_jclk : std_logic_vector(31 downto 0);

    -- ── v0.8 CRC sweep (REA-P2.2 increment 2): sample-plane engine reading
    --    dpram port A after done. sweep_rst aborts it on arm/soft reset so
    --    capture reclaims port A (REQ-803); the arbiter grants port A to the
    --    sweep only while busy (which only happens after done). ────────────
    signal sweep_rst        : std_logic;
    signal sweep_start      : std_logic;
    signal prev_done_r      : std_logic := '0';
    signal sweep_owns_a     : std_logic;
    signal sweep_busy       : std_logic;
    signal sweep_crc_done   : std_logic;
    signal sweep_crc_o      : std_logic_vector(31 downto 0);
    signal sweep_mem_addr   : std_logic_vector(C_PTR_W - 1 downto 0);
    signal sweep_mem_dout   : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal dpram_addr_a     : std_logic_vector(C_PTR_W - 1 downto 0);
    signal dpram_we_a       : std_logic;
    -- Intermediate crc_valid (set on sweep completion, cleared on abort). The
    -- proper snapshot/settle/toggle + epoch-suppress publication is increment 3
    -- (REQ-808); this simple valid + a held (set-once) CRC is coherent for a
    -- single capture (REQ-800/802).
    signal crc_sample_r     : std_logic_vector(31 downto 0) := (others => '0');
    signal crc_valid_r      : std_logic := '0';
    signal crc_sample_jclk  : std_logic_vector(31 downto 0);
    signal crc_valid_jclk   : std_logic_vector(0 downto 0);

    -- ── Comparator-array config (RTL-P3.647): regbank → CDC → FSM ──
    -- array_enable rides in trig_mode bit[2] (already CDC'd); only the
    -- per-condition value/mask/op/valid arrays need their own sync.
    signal cond_values_jclk : std_logic_vector(G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0);
    signal cond_masks_jclk  : std_logic_vector(G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0);
    signal cond_ops_jclk    : std_logic_vector(G_TRIG_CONDS * 4 - 1 downto 0);
    signal cond_valid_jclk  : std_logic_vector(G_TRIG_CONDS - 1 downto 0);
    signal cond_values_sclk : std_logic_vector(G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0);
    signal cond_masks_sclk  : std_logic_vector(G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0);
    signal cond_ops_sclk    : std_logic_vector(G_TRIG_CONDS * 4 - 1 downto 0);
    signal cond_valid_sclk  : std_logic_vector(G_TRIG_CONDS - 1 downto 0);

    -- ── FSM outputs (sample_clk_i domain) ──────────────────────────
    signal armed_sclk     : std_logic;
    signal triggered_sclk : std_logic;
    signal done_sclk      : std_logic;
    signal overflow_sclk  : std_logic;
    signal trigger_out_sclk : std_logic;
    signal trigger_sticky_r : std_logic := '0';
    signal dpram_we_sclk    : std_logic;
    signal dpram_addr_sclk  : std_logic_vector(C_PTR_W - 1 downto 0);
    signal dpram_din_sclk   : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal wr_ptr_sclk      : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_ptr_sclk    : std_logic_vector(C_PTR_W - 1 downto 0);
    signal start_ptr_sclk   : std_logic_vector(C_PTR_W - 1 downto 0);

    -- ── CDC: sample_clk_i → jtag_clk_i (status mirror) ───────────────
    signal armed_jclk     : std_logic_vector(0 downto 0);
    signal triggered_jclk : std_logic_vector(0 downto 0);
    signal done_jclk      : std_logic_vector(0 downto 0);
    signal overflow_jclk  : std_logic_vector(0 downto 0);
    signal start_ptr_jclk : std_logic_vector(C_PTR_W - 1 downto 0);

    -- ── DPRAM read-port (jtag_clk_i domain) ────────────────────────
    signal dpram_addr_b : std_logic_vector(C_PTR_W - 1 downto 0);
    signal dpram_dout_b : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    constant C_TIMESTAMP_STORAGE_W : positive := max_nat(1, G_TIMESTAMP_W);
    signal timestamp_dout_b : std_logic_vector(
        C_TIMESTAMP_STORAGE_W - 1 downto 0);

    -- ── reg_rdata_i mux: regbank vs dpram window ───────────────────
    signal regbank_rdata : std_logic_vector(31 downto 0);
    signal dpram_rdata   : std_logic_vector(31 downto 0);
    signal in_dpram_window : std_logic;

    function is_01(v : std_logic_vector) return boolean is
    begin
        for i in v'range loop
            if v(i) /= '0' and v(i) /= '1' then
                return false;
            end if;
        end loop;
        return true;
    end function;

    -- Forward declarations of CDC entities are not strictly needed in
    -- VHDL-93+ (entity instantiation works directly), but we keep
    -- them here for clarity.
    component rr_rea_sync_word is
        generic (G_WIDTH : positive);
        port (
            dst_clk_i : in  std_logic;
            din_i     : in  std_logic_vector(G_WIDTH - 1 downto 0);
            dout_o    : out std_logic_vector(G_WIDTH - 1 downto 0)
        );
    end component;

    component rr_rea_pulse_xfer is
        port (
            src_toggle_i : in  std_logic;
            dst_clk_i    : in  std_logic;
            dst_rst_i    : in  std_logic;
            dst_pulse_o  : out std_logic
        );
    end component;

begin

    -- ── JTAG protocol decoder ────────────────────────────────────
    u_jtag : entity work.rr_rea_jtag_iface
        port map (
            arst_i      => arst_i,
            tck_i       => tck_i,
            tdi_i       => tdi_i,
            tdo_o       => tdo_o,
            capture_i   => capture_i,
            shift_en_i  => shift_en_i,
            update_i    => update_i,
            sel_i       => sel_i,
            reg_clk_o   => reg_clk_o,
            reg_rst_o   => reg_rst_o,
            reg_wr_en_o => reg_wr_en_o,
            reg_rd_en_o => reg_rd_en_o,
            reg_addr_o  => reg_addr_o,
            reg_wdata_o => reg_wdata_o,
            reg_rdata_i => reg_rdata_i
        );

    -- ── Register file ────────────────────────────────────────────
    u_regbank : entity work.rr_rea_regbank
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN,
            G_TRIG_CONDS  => G_TRIG_CONDS,
            G_NUM_SOURCE  => G_NUM_SOURCE
        )
        port map (
            jtag_clk_i => reg_clk_o,
            jtag_rst_i => reg_rst_o,
            wr_en_i    => reg_wr_en_o,
            wr_addr_i  => reg_addr_o,
            wr_data_i  => reg_wdata_o,
            rd_addr_i  => reg_addr_o,
            rd_data_o  => regbank_rdata,
            armed_i     => armed_jclk(0),
            triggered_i => triggered_jclk(0),
            done_i      => done_jclk(0),
            overflow_i  => overflow_jclk(0),
            start_ptr_i => start_ptr_jclk,
            -- v0.8 readback integrity (REA-P2.2). CRC values + crc_valid arrive
            -- with the sweep (P2.2p2-sweep); selftest bits with fill (P2.3).
            -- Wired to their inert defaults until then; the epoch counter is live.
            crc_sample_i       => crc_sample_jclk,
            crc_ts_i           => (others => '0'),  -- timestamp-plane sweep: later sub-step
            capture_epoch_i    => capture_epoch_jclk,
            crc_valid_i        => crc_valid_jclk(0),
            selftest_busy_i    => '0',
            selftest_mode_i    => '0',
            selftest_refused_i => '0',
            pretrig_len_o  => pretrig_jclk,
            posttrig_len_o => posttrig_jclk,
            trig_value_o   => trig_value_jclk,
            trig_mask_o    => trig_mask_jclk,
            trig_mode_o    => trig_mode_jclk,
            chan_sel_o     => chan_sel_jclk,
            decim_ratio_o  => decim_ratio_jclk,
            data_word_sel_o => data_word_sel_jclk,
            data_plane_sel_o => data_plane_sel_jclk,
            cond_values_o  => cond_values_jclk,
            cond_masks_o   => cond_masks_jclk,
            cond_ops_o     => cond_ops_jclk,
            cond_valid_o   => cond_valid_jclk,
            source_o       => source_jclk,
            arm_toggle_o   => arm_toggle_jclk,
            reset_toggle_o => reset_toggle_jclk
        );

    -- ── reg_rdata_i mux: dpram window vs regbank ───────────────────
    -- dpram_window: addr in [0x0100 .. 0x0100 + DEPTH*4)
    -- (each dpram cell occupies 4 bytes / 1 word in the JTAG map)
    process (reg_addr_o)
        variable in_window_v : boolean;
    begin
        in_dpram_window <= '0';
        dpram_addr_b <= (others => '0');

        if is_01(reg_addr_o) then
            in_window_v :=
                unsigned(reg_addr_o) >= unsigned(C_ADDR_DATA_BASE) and
                unsigned(reg_addr_o) < (unsigned(C_ADDR_DATA_BASE) +
                                      to_unsigned(G_DEPTH * 4, 16));
            if in_window_v then
                in_dpram_window <= '1';
                dpram_addr_b <= std_logic_vector(resize(
                    shift_right(unsigned(reg_addr_o) - unsigned(C_ADDR_DATA_BASE), 2),
                    C_PTR_W));
            end if;
        else
            in_dpram_window <= 'X';
            dpram_addr_b <= (others => 'X');
        end if;
    end process;

    -- RTL-P1.91: DATA_WORD_SEL pages a full-width cell through the frozen
    -- one-address-per-cell DATA_BASE window. shift_right + resize naturally
    -- zero-pads the final partial word and returns zero out of range.
    -- DATA_PLANE_SEL (RTL-T2.123) picks the sample vs timestamp plane.
    --
    -- RTL-P1.96: the paging mux output is REGISTERED on reg_clk_o (tck_i) before
    -- the DR capture_i — at wide G_SAMPLE_W this shift is a ~22:1 word mux over
    -- the full sample width, the same class of comb cone that Quartus Pro
    -- 26.1/Arria 10 miscompiled at the regbank read-mux (bit0=1 reads
    -- captured as all-ones). DATA-window reads are therefore valid one
    -- reg_clk_o edge after dpram_dout_b (two after the read command commits);
    -- the two-scan read flow every host uses gives it several.
    process (reg_clk_o)
    begin
        if rising_edge(reg_clk_o) then
            if is_01(data_word_sel_jclk) then
                if data_plane_sel_jclk = '0' then
                    dpram_rdata <= std_logic_vector(resize(
                        shift_right(
                            unsigned(dpram_dout_b),
                            to_integer(unsigned(data_word_sel_jclk)) *
                                C_DATA_WORD_W),
                        C_DATA_WORD_W));
                elsif data_plane_sel_jclk = '1' then
                    dpram_rdata <= std_logic_vector(resize(
                        shift_right(
                            unsigned(timestamp_dout_b),
                            to_integer(unsigned(data_word_sel_jclk)) *
                                C_DATA_WORD_W),
                        C_DATA_WORD_W));
                else
                    dpram_rdata <= (others => 'X');
                end if;
            else
                dpram_rdata <= (others => 'X');
            end if;
        end if;
    end process;

    reg_rdata_i <= dpram_rdata when in_dpram_window = '1' else regbank_rdata;

    -- ── CDC: jtag_clk_i config words → sample_clk_i ──────────────────
    u_cdc_pretrig : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk_i => sample_clk_i, din_i => pretrig_jclk,
                  dout_o => pretrig_sclk);

    u_cdc_posttrig : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk_i => sample_clk_i, din_i => posttrig_jclk,
                  dout_o => posttrig_sclk);

    u_cdc_trig_value : rr_rea_sync_word
        generic map (G_WIDTH => G_SAMPLE_W)
        port map (dst_clk_i => sample_clk_i, din_i => trig_value_jclk,
                  dout_o => trig_value_sclk);

    u_cdc_trig_mask : rr_rea_sync_word
        generic map (G_WIDTH => G_SAMPLE_W)
        port map (dst_clk_i => sample_clk_i, din_i => trig_mask_jclk,
                  dout_o => trig_mask_sclk);

    -- Comparator-op + enable-bits CDC: low 16 bits of TRIG_MODE carry
    -- value_match[0]/seq_en[1]/array_en[2]/ext_en[3], the op nibble [7:4]
    -- (RTL-P3.644/645/646), and ext_and[8] (RTL-P3.266). Quasi-static —
    -- the host writes TRIG_MODE before pulsing arm.
    u_cdc_trig_mode : rr_rea_sync_word
        generic map (G_WIDTH => 16)
        port map (dst_clk_i => sample_clk_i, din_i => trig_mode_jclk(15 downto 0),
                  dout_o => trig_mode_sclk);

    -- RTL-P3.266: external board-pin trigger, synced into the sample_clk_i
    -- domain (the pin is fully asynchronous to sample_clk_i — the user routes
    -- it from a package pin). A double-flop level synchronizer is correct for
    -- a level / wide-pulse external trigger; sub-sample-period pulses are not
    -- guaranteed to be seen (documented in SPEC).
    u_cdc_ext_trig : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => sample_clk_i, din_i => (0 => ext_trigger_i),
                  dout_o => ext_trig_sclk);

    -- v0.3 decimation ratio CDC
    u_cdc_decim : rr_rea_sync_word
        generic map (G_WIDTH => 24)
        port map (dst_clk_i => sample_clk_i, din_i => decim_ratio_jclk,
                  dout_o => decim_ratio_sclk);

    -- RTL-P3.647 comparator-array config CDC. Quasi-static (the host writes
    -- all slots before pulsing arm; the FSM latches on the arm pulse), so the
    -- per-bit double-flop sync — same as trig_value/mask — is sufficient.
    u_cdc_cond_values : rr_rea_sync_word
        generic map (G_WIDTH => G_TRIG_CONDS * G_SAMPLE_W)
        port map (dst_clk_i => sample_clk_i, din_i => cond_values_jclk,
                  dout_o => cond_values_sclk);
    u_cdc_cond_masks : rr_rea_sync_word
        generic map (G_WIDTH => G_TRIG_CONDS * G_SAMPLE_W)
        port map (dst_clk_i => sample_clk_i, din_i => cond_masks_jclk,
                  dout_o => cond_masks_sclk);
    u_cdc_cond_ops : rr_rea_sync_word
        generic map (G_WIDTH => G_TRIG_CONDS * 4)
        port map (dst_clk_i => sample_clk_i, din_i => cond_ops_jclk,
                  dout_o => cond_ops_sclk);
    u_cdc_cond_valid : rr_rea_sync_word
        generic map (G_WIDTH => G_TRIG_CONDS)
        port map (dst_clk_i => sample_clk_i, din_i => cond_valid_jclk,
                  dout_o => cond_valid_sclk);

    -- RTL-P2.837 write-side source CDC. The SOURCE register is quasi-static
    -- (the host writes it, then it holds — same profile as trig_value/mask and
    -- the cond_* arrays), so the per-bit two-flop rr_rea_sync_word is the right
    -- and sufficient synchronizer. It is emphatically NOT a bespoke single flop:
    -- a lone flop fanning out to multiple downstream gates can latch
    -- inconsistent values before a metastable flop resolves, so every source
    -- bit gets the proven ASYNC_REG double-flop (REA-REQ-020). The sync flops
    -- init to 0 (GSR), so source_o holds the gated DUT signal SAFE until the
    -- host writes SOURCE — matching the regbank reset default (no auto-release).
    u_cdc_source : rr_rea_sync_word
        generic map (G_WIDTH => G_NUM_SOURCE)
        port map (dst_clk_i => sample_clk_i, din_i => source_jclk,
                  dout_o => source_sclk);
    source_o <= source_sclk;

    probe_sclk <= probe_i;

    -- ── CDC: jtag_clk_i pulse toggles → sample_clk_i pulses ─────────
    u_cdc_arm : rr_rea_pulse_xfer
        port map (
            src_toggle_i => arm_toggle_jclk,
            dst_clk_i => sample_clk_i, dst_rst_i => sample_rst_i,
            dst_pulse_o => arm_pulse_sclk
        );

    u_cdc_reset : rr_rea_pulse_xfer
        port map (
            src_toggle_i => reset_toggle_jclk,
            dst_clk_i => sample_clk_i, dst_rst_i => sample_rst_i,
            dst_pulse_o => reset_pulse_sclk
        );

    -- ── CAPTURE_EPOCH counter (REA-P2.2, REQ-807) ────────────────────
    -- Bumps on exactly: accepted arm, soft reset (and sweep abort / accepted
    -- fill once those land). sample_rst_i is the hard reset to 0. Nothing else
    -- moves it — a completed sweep / JTAG read / refused op does NOT.
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            capture_epoch_r <= (others => '0');
        elsif rising_edge(sample_clk_i) then
            if arm_pulse_sclk = '1' or reset_pulse_sclk = '1' then
                capture_epoch_r <= std_logic_vector(unsigned(capture_epoch_r) + 1);
            end if;
        end if;
    end process;

    -- CAPTURE_EPOCH crossed sample_clk_i → jtag_clk_i (reg_clk_o) for regbank
    -- readback. A word sync is adequate: the host reads it, brackets a window
    -- read, and re-reads — a one-off skewed sample self-corrects on re-read.
    u_cdc_epoch : rr_rea_sync_word
        generic map (G_WIDTH => 32)
        port map (dst_clk_i => reg_clk_o, din_i => capture_epoch_r,
                  dout_o => capture_epoch_jclk);

    -- ── CRC sweep (REA-P2.2 increment 2) ─────────────────────────────
    -- Abort on arm / soft reset so capture reclaims port A (REQ-803). Driven
    -- synchronously (the pulses are registered), so this is a synchronous
    -- reset assertion — no async-deassertion metastability.
    sweep_rst <= sample_rst_i or arm_pulse_sclk or reset_pulse_sclk;

    -- Start a sweep on the rising edge of done (capture just completed).
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            prev_done_r <= '0';
        elsif rising_edge(sample_clk_i) then
            prev_done_r <= done_sclk;
        end if;
    end process;
    sweep_start <= done_sclk and not prev_done_r;

    -- Arbiter: the sweep owns port A only while it is busy, which by
    -- construction is only after done (writes are off then). An arm/reset
    -- aborts the sweep (sweep_rst) → busy drops → capture reclaims port A.
    sweep_owns_a <= sweep_busy;
    dpram_addr_a <= sweep_mem_addr when sweep_owns_a = '1' else dpram_addr_sclk;
    dpram_we_a   <= '0'            when sweep_owns_a = '1' else dpram_we_sclk;

    u_crc_sweep : entity work.rr_rea_crc_sweep
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk_i => sample_clk_i,
            sample_rst_i => sweep_rst,
            start_i      => sweep_start,
            mem_dout_i   => sweep_mem_dout,
            mem_addr_o   => sweep_mem_addr,
            mem_rd_en_o  => open,
            busy_o       => sweep_busy,
            crc_done_o   => sweep_crc_done,
            crc_o        => sweep_crc_o
        );

    -- Latch the CRC + set the (intermediate) valid on sweep completion; both
    -- clear on abort. The CRC is set-once and held, so the plain word/bit sync
    -- below is coherent for a single capture (proper snapshot/toggle + epoch
    -- suppress is increment 3, REQ-808).
    process (sample_clk_i, sweep_rst)
    begin
        if sweep_rst = '1' then
            crc_sample_r <= (others => '0');
            crc_valid_r  <= '0';
        elsif rising_edge(sample_clk_i) then
            if sweep_crc_done = '1' then
                crc_sample_r <= sweep_crc_o;
                crc_valid_r  <= '1';
            end if;
        end if;
    end process;

    u_cdc_crc_sample : rr_rea_sync_word
        generic map (G_WIDTH => 32)
        port map (dst_clk_i => reg_clk_o, din_i => crc_sample_r,
                  dout_o => crc_sample_jclk);
    u_cdc_crc_valid : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => reg_clk_o, din_i(0) => crc_valid_r,
                  dout_o => crc_valid_jclk);

    -- ── Capture FSM ──────────────────────────────────────────────
    u_fsm : entity work.rr_rea_capture_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH,
                     G_TRIG_CONDS => G_TRIG_CONDS)
        port map (
            sample_clk_i    => sample_clk_i,
            sample_rst_i    => sample_rst_i,
            probe_i      => probe_sclk,
            arm_pulse_i     => arm_pulse_sclk,
            reset_pulse_i   => reset_pulse_sclk,
            pretrig_len_i  => pretrig_sclk,
            posttrig_len_i => posttrig_sclk,
            trig_value_i   => trig_value_sclk,
            trig_mask_i    => trig_mask_sclk,
            trig_mode_i    => trig_mode_sclk(7 downto 0),
            decim_ratio_i  => decim_ratio_sclk,
            -- RTL-P3.647: array_enable rides in trig_mode bit[2] (CDC'd above);
            -- the per-condition arrays come from their own sync.
            array_enable_i => trig_mode_sclk(C_TRIG_MODE_BIT_ARRAY_EN),
            cond_values_i  => cond_values_sclk,
            cond_masks_i   => cond_masks_sclk,
            cond_ops_i     => cond_ops_sclk,
            cond_valid_i   => cond_valid_sclk,
            -- RTL-P3.266: external board-pin trigger (synced) + its enable
            -- (TRIG_MODE bit[3]) and OR/AND combine mode (bit[8]).
            ext_trigger_i  => ext_trig_sclk(0),
            ext_enable_i   => trig_mode_sclk(C_TRIG_MODE_BIT_EXT_EN),
            ext_and_i      => trig_mode_sclk(C_TRIG_MODE_BIT_EXT_AND),
            armed_o       => armed_sclk,
            triggered_o   => triggered_sclk,
            done_o        => done_sclk,
            overflow_o    => overflow_sclk,
            trigger_o => trigger_out_sclk,
            dpram_we_o    => dpram_we_sclk,
            dpram_addr_o  => dpram_addr_sclk,
            dpram_din_o   => dpram_din_sclk,
            wr_ptr_o    => wr_ptr_sclk,
            trig_ptr_o  => trig_ptr_sclk,
            start_ptr_o => start_ptr_sclk
        );

    -- ── CDC: sample_clk_i status → jtag_clk_i ────────────────────────
    u_cdc_armed : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => reg_clk_o,
                  din_i(0) => armed_sclk,
                  dout_o => armed_jclk);

    u_cdc_triggered : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => reg_clk_o,
                  din_i(0) => triggered_sclk,
                  dout_o => triggered_jclk);

    u_cdc_done : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => reg_clk_o,
                  din_i(0) => done_sclk,
                  dout_o => done_jclk);

    u_cdc_overflow : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk_i => reg_clk_o,
                  din_i(0) => overflow_sclk,
                  dout_o => overflow_jclk);

    u_cdc_start_ptr : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk_i => reg_clk_o,
                  din_i => start_ptr_sclk,
                  dout_o => start_ptr_jclk);

    -- ── trigger_o: pulse the external port + maintain a sticky
    --    flag flipped on each trigger so an LED can dance and the
    --    Vivado optimizer can't prune the hierarchy. ─────────────
    trigger_o <= trigger_sticky_r;
    process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            trigger_sticky_r <= '0';
        elsif rising_edge(sample_clk_i) then
            if trigger_out_sclk = '1' then
                trigger_sticky_r <= not trigger_sticky_r;
            end if;
        end if;
    end process;

    -- ── Sample DPRAM ─────────────────────────────────────────────
    u_dpram : entity work.rr_rea_dpram
        generic map (G_WIDTH => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            clk_a_i  => sample_clk_i,
            we_a_i   => dpram_we_a,      -- arbitrated: capture writes / sweep reads
            addr_a_i => dpram_addr_a,    -- muxed: capture ptr / sweep addr
            din_a_i  => dpram_din_sclk,
            dout_a_o => sweep_mem_dout,  -- port-A read → CRC sweep engine
            clk_b_i  => reg_clk_o,
            addr_b_i => dpram_addr_b,
            dout_b_o => dpram_dout_b
        );

    -- RTL-T2.123: a free-running sample-clock counter is written through the
    -- exact same WE/address pair as the sample plane. Each stored sample cell
    -- therefore has one aligned timestamp, including across ring wrap and
    -- decimation gaps. Soft capture_i reset preserves time continuity; only
    -- sample_rst_i restarts the counter. G_TIMESTAMP_W=0 elaborates no plane.
    g_timestamp_plane : if G_TIMESTAMP_W > 0 generate
        signal timestamp_r : unsigned(G_TIMESTAMP_W - 1 downto 0) :=
            (others => '0');
        signal timestamp_dout : std_logic_vector(G_TIMESTAMP_W - 1 downto 0);
    begin
        process (sample_clk_i, sample_rst_i)
        begin
            if sample_rst_i = '1' then
                timestamp_r <= (others => '0');
            elsif rising_edge(sample_clk_i) then
                timestamp_r <= timestamp_r + 1;
            end if;
        end process;

        u_timestamp_dpram : entity work.rr_rea_dpram
            generic map (G_WIDTH => G_TIMESTAMP_W, G_DEPTH => G_DEPTH)
            port map (
                clk_a_i  => sample_clk_i,
                we_a_i   => dpram_we_sclk,
                addr_a_i => dpram_addr_sclk,
                din_a_i  => std_logic_vector(timestamp_r),
                dout_a_o => open,
                clk_b_i  => reg_clk_o,
                addr_b_i => dpram_addr_b,
                dout_b_o => timestamp_dout
            );
        timestamp_dout_b <= timestamp_dout;
    end generate;

    g_no_timestamp_plane : if G_TIMESTAMP_W = 0 generate
        timestamp_dout_b <= (others => '0');
    end generate;

end architecture;
