-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_capture_fsm — sliding-window capture state machine.
--
-- THIS IS WHERE WE EXPLICITLY DIVERGE FROM fcapz.
--
-- The dpram write path is FREE-RUNNING from sample_rst deassertion:
-- `dpram_we` is `!done && store_enable_in`, NOT gated by `armed`.
-- Combined with `wr_ptr` that increments every cycle (also not gated
-- by `armed`), this implements the textbook ILA sliding-window model
-- (Vivado ChipScope, Intel SignalTap, ARM ELA): the buffer always
-- holds the most-recent DEPTH samples, so a trigger that fires
-- immediately after `arm` still has the full pretrigger window of
-- context already in the buffer.
--
-- fcapz's `mem_we_a = armed && !done && store_enable` leaves uninit
-- BRAM cells in the captured window when the trigger fires before
-- pretrig_len cycles have elapsed since arm. We do not ship that.
--
-- v0.1 simplification: store_enable_in is unused (tied high by the
-- top-level for now). v0.3 brings decimation and storage
-- qualification, at which point this port carries the gate.
--
-- See requirements.yml REA-REQ-100..106 for the test contract.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_capture_fsm is
    generic (
        G_SAMPLE_W     : positive := 12;
        G_DEPTH        : positive := 4096;
        G_TRIG_STAGES  : positive := 1;  -- v0.3 sequencer depth (REA-REQ-607)
        G_TRIG_CONDS   : positive := 4   -- v0.5 comparator-array slots (P3.647)
    );
    port (
        sample_clk  : in  std_logic;
        sample_rst  : in  std_logic;

        -- ── Probe input (sync'd to sample_clk by the caller) ─────
        probe_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Control pulses (sync'd to sample_clk by rr_rea_cdc) ──
        arm_pulse   : in  std_logic;   -- 1 cycle wide
        reset_pulse : in  std_logic;   -- 1 cycle wide; clears state

        -- ── External trigger input (REA-REQ-400) ─────────────────
        -- 1-cycle pulse on sample_clk from the cross-domain trigger
        -- crossbar (rr_rea_trig_xbar) — when armed, fires the
        -- capture as if the local comparator hit. Does NOT drive
        -- trigger_out (that would create a ping-pong loop with
        -- other REA instances on the bus). Tied low when the
        -- crossbar isn't connected.
        trigger_in  : in  std_logic := '0';

        -- ── Latched config (sample_clk domain) ───────────────────
        pretrig_len_in  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_in : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_in   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- Comparator op for the legacy single-comparator path (RTL-P3.644/
        -- 645/646): bits[7:4] of TRIG_MODE = C_TRIG_OP_{EQ,NE,LT,GT,RISE,FALL}.
        -- Default 0 → EQ → historical masked-equality behaviour (back-compat).
        trig_mode_in    : in  std_logic_vector(7 downto 0) := (others => '0');
        -- v0.3 decimation: capture every (decim_ratio + 1) samples.
        -- Tied 0 disables decimation (every sample stored). Latched
        -- on arm_pulse like the other config.
        decim_ratio_in  : in  std_logic_vector(23 downto 0)
                              := (others => '0');

        -- ── v0.3 multi-stage sequencer (REA-REQ-600..607) ────────
        -- seq_enable_in selects between the legacy single-comparator
        -- path (trig_value_in / trig_mask_in) and the per-stage
        -- sequencer below. Tied 0 → legacy path (REA-REQ-600).
        seq_enable_in     : in  std_logic := '0';

        -- Per-stage value/mask/count_target arrays packed into flat
        -- vectors so the entity stays VHDL-93-compatible. Each
        -- stage K occupies bits [(K+1)*W - 1 : K*W] in its respective
        -- vector. SAMPLE_W bits per stage for value/mask, 16 bits
        -- per stage for count_target.
        --
        -- RTL-P3.691: the per-stage value/mask are FULL G_SAMPLE_W wide
        -- here (bounded by the C_MAX_SAMPLE_W assert below, same as the
        -- legacy path) — there is no 32-bit cap on the FSM side. When the
        -- regbank's per-stage value_a/mask_a JTAG slots are eventually
        -- added they must page these full-width fields (see the WIDTH
        -- CONTRACT note in rr_rea_pkg), never truncate them to 32 bits.
        seq_values_in     : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_masks_in      : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_counts_in     : in  std_logic_vector(
            G_TRIG_STAGES * 16 - 1 downto 0)
                              := (others => '0');

        -- ── v0.5 per-condition comparator array (RTL-P3.647) ─────
        -- array_enable_in selects the AND-of-conditions path: each valid
        -- slot k applies its own op (cond_ops_in[k*4 +: 4] = C_TRIG_OP_*)
        -- to the masked field (probe_in and cond_masks_in[k]) vs
        -- (cond_values_in[k] and cond_masks_in[k]); the trigger fires when
        -- ALL valid slots match (mixed-op AND). Invalid slots don't block.
        -- value/mask are full G_SAMPLE_W (the regbank expands a compact
        -- 32-bit {op,lsb,width,value} slot into the shifted field). Tied 0 →
        -- legacy/seq path (back-compat). seq_enable takes precedence.
        array_enable_in   : in  std_logic := '0';
        cond_values_in    : in  std_logic_vector(
            G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        cond_masks_in     : in  std_logic_vector(
            G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        cond_ops_in       : in  std_logic_vector(
            G_TRIG_CONDS * 4 - 1 downto 0)
                              := (others => '0');
        cond_valid_in     : in  std_logic_vector(G_TRIG_CONDS - 1 downto 0)
                              := (others => '0');

        -- ── v0.5 external board-pin trigger (RTL-P3.266) ─────────
        -- ext_trigger_in is a package-pin input (synced to sample_clk in
        -- rr_rea_top) the user routes from a board pin — an oscilloscope
        -- trigger-out, another FPGA's trigger_out, a push-button, etc.
        -- When ext_enable_in='1' it joins the fire decision:
        --   ext_and_in='0' (OR)  → fire on (internal hit) OR (ext pin)
        --   ext_and_in='1' (AND) → fire only on (internal hit) AND (ext pin)
        -- Tied 0 / disabled → the pin is ignored (internal-only path,
        -- back-compat). Distinct from the trig_xbar `trigger_in` pulse below,
        -- which stays an independent OR regardless of ext mode.
        ext_trigger_in    : in  std_logic := '0';
        ext_enable_in     : in  std_logic := '0';
        ext_and_in        : in  std_logic := '0';

        -- ── Status flags (combinational from registers) ──────────
        armed       : out std_logic;
        triggered   : out std_logic;
        done        : out std_logic;
        overflow    : out std_logic;
        trigger_out : out std_logic;   -- 1-cycle pulse on local fire

        -- ── DPRAM port-A drive ───────────────────────────────────
        dpram_we    : out std_logic;
        dpram_addr  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dpram_din   : out std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Pointer outputs (regbank readback) ───────────────────
        wr_ptr_out    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_ptr_out  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        start_ptr_out : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0)
    );
end entity;

architecture rtl of rr_rea_capture_fsm is

    constant C_PTR_W : positive := clog2(G_DEPTH);
    constant C_WIDTH_STAGES : positive :=
        (G_SAMPLE_W + C_SLICE_W - 1) / C_SLICE_W;
    constant C_REDUCE_STAGES : natural :=
        clog2(G_TRIG_CONDS);
    constant C_PIPE_STAGES : positive := C_WIDTH_STAGES + C_REDUCE_STAGES;
    constant C_NUM_COMPARATORS : positive :=
        1 + G_TRIG_CONDS + G_TRIG_STAGES;
    constant C_COND_BASE : positive := 1;
    constant C_SEQ_BASE : positive := C_COND_BASE + G_TRIG_CONDS;
    constant C_REDUCE_WIDTH : positive := 2 ** C_REDUCE_STAGES;
    constant C_REDUCE_STORAGE : positive := max_nat(1, C_REDUCE_STAGES);
    constant C_CMP_EQUAL : t_cmp_token := (eq => '1', gt => '0', lt => '0');

    function cmp_masked_slice(
        probe_value : std_logic_vector;
        match_value : std_logic_vector;
        match_mask  : std_logic_vector;
        slice_index : natural
    ) return t_cmp_token is
        variable masked_probe : std_logic_vector(C_SLICE_W - 1 downto 0) :=
            (others => '0');
        variable masked_value : std_logic_vector(C_SLICE_W - 1 downto 0) :=
            (others => '0');
        variable source_index : natural;
    begin
        for bit_index in 0 to C_SLICE_W - 1 loop
            source_index := slice_index * C_SLICE_W + bit_index;
            if source_index < probe_value'length then
                masked_probe(bit_index) :=
                    probe_value(probe_value'low + source_index) and
                    match_mask(match_mask'low + source_index);
                masked_value(bit_index) :=
                    match_value(match_value'low + source_index) and
                    match_mask(match_mask'low + source_index);
            end if;
        end loop;
        return cmp_slice(masked_probe, masked_value);
    end function;

    function rise_masked_slice(
        current_value  : std_logic_vector;
        previous_value : std_logic_vector;
        match_mask     : std_logic_vector;
        slice_index    : natural
    ) return std_logic is
        variable result : std_logic := '0';
        variable source_index : natural;
    begin
        for bit_index in 0 to C_SLICE_W - 1 loop
            source_index := slice_index * C_SLICE_W + bit_index;
            if source_index < current_value'length
               and match_mask(match_mask'low + source_index) = '1'
               and previous_value(previous_value'low + source_index) = '0'
               and current_value(current_value'low + source_index) = '1' then
                result := '1';
            end if;
        end loop;
        return result;
    end function;

    function fall_masked_slice(
        current_value  : std_logic_vector;
        previous_value : std_logic_vector;
        match_mask     : std_logic_vector;
        slice_index    : natural
    ) return std_logic is
        variable result : std_logic := '0';
        variable source_index : natural;
    begin
        for bit_index in 0 to C_SLICE_W - 1 loop
            source_index := slice_index * C_SLICE_W + bit_index;
            if source_index < current_value'length
               and match_mask(match_mask'low + source_index) = '1'
               and previous_value(previous_value'low + source_index) = '1'
               and current_value(current_value'low + source_index) = '0' then
                result := '1';
            end if;
        end loop;
        return result;
    end function;

    function token_matches(
        token_value : t_cmp_token;
        rise_value  : std_logic;
        fall_value  : std_logic;
        operation   : natural
    ) return std_logic is
    begin
        case operation is
            when C_TRIG_OP_EQ => return token_value.eq;
            when C_TRIG_OP_NE => return not token_value.eq;
            when C_TRIG_OP_LT => return token_value.lt;
            when C_TRIG_OP_GT => return token_value.gt;
            when C_TRIG_OP_RISE => return rise_value;
            when C_TRIG_OP_FALL => return fall_value;
            when others => return '0';
        end case;
    end function;

    type t_sample_pipe is array (0 to C_WIDTH_STAGES - 1)
        of std_logic_vector(G_SAMPLE_W - 1 downto 0);
    type t_ptr_pipe is array (0 to C_WIDTH_STAGES - 1)
        of unsigned(C_PTR_W - 1 downto 0);
    type t_token_pipe is array (
        0 to C_WIDTH_STAGES - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of t_cmp_token;
    type t_edge_pipe is array (
        0 to C_WIDTH_STAGES - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of std_logic;
    type t_condition_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic_vector(C_REDUCE_WIDTH - 1 downto 0);
    type t_bit_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic;
    type t_seq_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic_vector(G_TRIG_STAGES - 1 downto 0);
    type t_ptr_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of unsigned(C_PTR_W - 1 downto 0);

    signal probe_pipe_r : t_sample_pipe := (others => (others => '0'));
    signal previous_pipe_r : t_sample_pipe := (others => (others => '0'));
    signal pointer_width_r : t_ptr_pipe := (others => (others => '0'));
    signal token_width_r : t_token_pipe :=
        (others => (others => C_CMP_EQUAL));
    signal rise_width_r : t_edge_pipe := (others => (others => '0'));
    signal fall_width_r : t_edge_pipe := (others => (others => '0'));
    signal valid_width_r : std_logic_vector(C_WIDTH_STAGES - 1 downto 0) :=
        (others => '0');
    signal ext_width_r : std_logic_vector(C_WIDTH_STAGES - 1 downto 0) :=
        (others => '0');

    signal legacy_width_match : std_logic;
    signal cond_width_match : std_logic_vector(G_TRIG_CONDS - 1 downto 0);
    signal seq_width_match : std_logic_vector(G_TRIG_STAGES - 1 downto 0);

    signal condition_reduce_r : t_condition_reduce_pipe :=
        (others => (others => '1'));
    signal legacy_reduce_r : t_bit_reduce_pipe := (others => '0');
    signal seq_reduce_r : t_seq_reduce_pipe := (others => (others => '0'));
    signal valid_reduce_r : t_bit_reduce_pipe := (others => '0');
    signal ext_reduce_r : t_bit_reduce_pipe := (others => '0');
    signal pointer_reduce_r : t_ptr_reduce_pipe :=
        (others => (others => '0'));

    signal legacy_final_match : std_logic;
    signal array_final_match : std_logic;
    signal seq_final_match : std_logic_vector(G_TRIG_STAGES - 1 downto 0);
    signal pipeline_final_valid : std_logic;
    signal pipeline_final_ext : std_logic;
    signal pipeline_final_ptr : unsigned(C_PTR_W - 1 downto 0);

    signal armed_r       : std_logic := '0';
    signal triggered_r   : std_logic := '0';
    signal done_r        : std_logic := '0';
    signal overflow_r    : std_logic := '0';
    signal wr_ptr_r      : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal trig_ptr_r    : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal start_ptr_r   : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal post_count_r  : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal pretrig_len_r : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal posttrig_len_r: unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal decim_ratio_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_count_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_tick    : std_logic;

    -- ── Sequencer state (v0.3, REA-REQ-600..607) ─────────────────
    -- seq_state_r tracks the current stage (0..G_TRIG_STAGES-1).
    -- seq_counters_r[K] counts cumulative matches for stage K and
    -- resets when seq_state advances past K (or on arm).
    -- Per-stage value/mask/count are LATCHED on arm_pulse just
    -- like the legacy comparator config; this keeps mid-capture
    -- changes from disturbing an in-flight sequence.
    constant C_SEQ_STATE_W : positive :=
        clog2(G_TRIG_STAGES + 1);    -- +1 so we can express FINAL+1
    -- Flat vectors for the per-stage config and counter state.
    -- Avoids array-of-vector slicing inside a clocked process loop,
    -- which nvc (1.18) handled inconsistently — the latch from the
    -- input port to the array element silently dropped to zero.
    -- Flat copies sidestep that and let the per-stage slice happen
    -- only in pure-combinational generate blocks below.
    signal seq_value_r_flat : std_logic_vector(
        G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal seq_mask_r_flat  : std_logic_vector(
        G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal seq_count_target_r_flat : std_logic_vector(
        G_TRIG_STAGES * 16 - 1 downto 0) := (others => '0');
    signal seq_counter_r_flat : std_logic_vector(
        G_TRIG_STAGES * 16 - 1 downto 0) := (others => '0');

    -- Per-stage views via generate (combinational slices).
    type t_seq_count_array is array (0 to G_TRIG_STAGES - 1)
        of unsigned(15 downto 0);
    signal seq_count_target_view : t_seq_count_array;
    signal seq_counter_view      : t_seq_count_array;
    signal seq_state_r   : unsigned(C_SEQ_STATE_W - 1 downto 0)
                              := (others => '0');
    signal seq_enable_r  : std_logic := '0';

    -- ── Comparator-array state (v0.5, RTL-P3.647) ────────────────
    -- Latched on arm like the seq/legacy config. Flat vectors (same
    -- nvc-safe pattern as the sequencer); per-condition slices live only
    -- in the pure-combinational generate below.
    signal array_enable_r : std_logic := '0';
    signal cond_values_r  : std_logic_vector(
        G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal cond_masks_r   : std_logic_vector(
        G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal cond_ops_r     : std_logic_vector(
        G_TRIG_CONDS * 4 - 1 downto 0) := (others => '0');
    signal cond_valid_r   : std_logic_vector(G_TRIG_CONDS - 1 downto 0)
                              := (others => '0');
    constant C_CONDS_NONE : std_logic_vector(G_TRIG_CONDS - 1 downto 0)
                              := (others => '0');

    -- "We just hit the final-stage's required count" — drives
    -- triggered_r when seq_enable_r is on (REA-REQ-602).
    signal seq_final_fire : std_logic;
    signal trig_value_r  : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trig_mask_r   : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trigger_out_r : std_logic := '0';

    -- ── Legacy-path comparator op (RTL-P3.644/645/646) ──────────
    -- trig_mode_r holds bits[7:0] of TRIG_MODE; bits[7:4] select the op.
    -- probe_prev_r is the previous sample, for edge detection.
    signal trig_mode_r  : std_logic_vector(7 downto 0) := (others => '0');
    signal probe_prev_r : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trig_op      : natural range 0 to 15 := 0;
    signal trigger_hit : std_logic;
    signal local_fire_pipe : std_logic;
    signal local_fire_ptr  : unsigned(C_PTR_W - 1 downto 0);

    -- ── External board-pin trigger (RTL-P3.266) ─────────────────
    -- ext_enable_r/ext_and_r latch on arm (like seq/array enables).
    -- ext_trig_r double-registers the (already sample_clk-synced)
    -- ext_trigger_in for clean edge timing alongside the comparator.
    -- effective_internal folds the external pin into the internal hit
    -- per the OR/AND mode; the FSM then fires on it (plus the
    -- independent trig_xbar OR).
    signal ext_enable_r      : std_logic := '0';
    signal ext_and_r         : std_logic := '0';
    signal ext_trig_r        : std_logic := '0';
    signal effective_internal : std_logic;

    -- RTL-P2.658(b): the trig value/mask are now banked into ceil(W/32)
    -- 32-bit JTAG words (paged via TRIG_WORD_SEL), so probes wider than
    -- 32 bits are supported up to C_MAX_SAMPLE_W. Still fail fast at
    -- elaboration past the ceiling rather than emit an opaque 'array
    -- index out of range' deep in synth.
    constant C_SAMPLE_W_OK : boolean := (G_SAMPLE_W <= C_MAX_SAMPLE_W);

begin

    assert C_SAMPLE_W_OK
        report "rr_rea: G_SAMPLE_W exceeds C_MAX_SAMPLE_W (256) - the banked "
             & "trigger value/mask supports up to 8 words; cap the probe "
             & "(RTL-P2.658)."
        severity failure;

    -- ── Per-stage views (combinational slices of flat vectors) ──
    g_seq_views : for k in 0 to G_TRIG_STAGES - 1 generate
        seq_count_target_view(k) <= unsigned(seq_count_target_r_flat(
            k * 16 + 15 downto k * 16));
        seq_counter_view(k) <= unsigned(seq_counter_r_flat(
            k * 16 + 15 downto k * 16));
    end generate;

    trig_op <= to_integer(unsigned(trig_mode_r(7 downto 4)));

    process (sample_clk, sample_rst)
    begin
        if sample_rst = '1' then
            probe_pipe_r <= (others => (others => '0'));
            previous_pipe_r <= (others => (others => '0'));
            pointer_width_r <= (others => (others => '0'));
            token_width_r <= (others => (others => C_CMP_EQUAL));
            rise_width_r <= (others => (others => '0'));
            fall_width_r <= (others => (others => '0'));
            valid_width_r <= (others => '0');
            ext_width_r <= (others => '0');
        elsif rising_edge(sample_clk) then
            if reset_pulse = '1' or arm_pulse = '1' then
                token_width_r <= (others => (others => C_CMP_EQUAL));
                rise_width_r <= (others => (others => '0'));
                fall_width_r <= (others => (others => '0'));
                valid_width_r <= (others => '0');
                ext_width_r <= (others => '0');
            else
                probe_pipe_r(0) <= probe_in;
                previous_pipe_r(0) <= probe_prev_r;
                pointer_width_r(0) <= wr_ptr_r;
                valid_width_r(0) <= armed_r and not triggered_r and not done_r;
                ext_width_r(0) <= ext_trig_r;

                token_width_r(0, 0) <= cmp_masked_slice(
                    probe_in, trig_value_r, trig_mask_r, 0);
                rise_width_r(0, 0) <= rise_masked_slice(
                    probe_in, probe_prev_r, trig_mask_r, 0);
                fall_width_r(0, 0) <= fall_masked_slice(
                    probe_in, probe_prev_r, trig_mask_r, 0);

                for condition_index in 0 to G_TRIG_CONDS - 1 loop
                    token_width_r(0, C_COND_BASE + condition_index) <=
                        cmp_masked_slice(
                            probe_in,
                            cond_values_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            0
                        );
                    rise_width_r(0, C_COND_BASE + condition_index) <=
                        rise_masked_slice(
                            probe_in,
                            probe_prev_r,
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            0
                        );
                    fall_width_r(0, C_COND_BASE + condition_index) <=
                        fall_masked_slice(
                            probe_in,
                            probe_prev_r,
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            0
                        );
                end loop;

                for sequence_index in 0 to G_TRIG_STAGES - 1 loop
                    token_width_r(0, C_SEQ_BASE + sequence_index) <=
                        cmp_masked_slice(
                            probe_in,
                            seq_value_r_flat(
                                sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto sequence_index * G_SAMPLE_W),
                            seq_mask_r_flat(
                                sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto sequence_index * G_SAMPLE_W),
                            0
                        );
                    rise_width_r(0, C_SEQ_BASE + sequence_index) <= '0';
                    fall_width_r(0, C_SEQ_BASE + sequence_index) <= '0';
                end loop;

                for width_stage in 1 to C_WIDTH_STAGES - 1 loop
                    probe_pipe_r(width_stage) <= probe_pipe_r(width_stage - 1);
                    previous_pipe_r(width_stage) <=
                        previous_pipe_r(width_stage - 1);
                    pointer_width_r(width_stage) <=
                        pointer_width_r(width_stage - 1);
                    valid_width_r(width_stage) <= valid_width_r(width_stage - 1);
                    ext_width_r(width_stage) <= ext_width_r(width_stage - 1);

                    token_width_r(width_stage, 0) <= cmp_combine(
                        cmp_masked_slice(
                            probe_pipe_r(width_stage - 1),
                            trig_value_r,
                            trig_mask_r,
                            width_stage
                        ),
                        token_width_r(width_stage - 1, 0)
                    );
                    rise_width_r(width_stage, 0) <=
                        rise_width_r(width_stage - 1, 0) or
                        rise_masked_slice(
                            probe_pipe_r(width_stage - 1),
                            previous_pipe_r(width_stage - 1),
                            trig_mask_r,
                            width_stage
                        );
                    fall_width_r(width_stage, 0) <=
                        fall_width_r(width_stage - 1, 0) or
                        fall_masked_slice(
                            probe_pipe_r(width_stage - 1),
                            previous_pipe_r(width_stage - 1),
                            trig_mask_r,
                            width_stage
                        );

                    for condition_index in 0 to G_TRIG_CONDS - 1 loop
                        token_width_r(
                            width_stage,
                            C_COND_BASE + condition_index
                        ) <= cmp_combine(
                            cmp_masked_slice(
                                probe_pipe_r(width_stage - 1),
                                cond_values_r(
                                    condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                    downto condition_index * G_SAMPLE_W),
                                cond_masks_r(
                                    condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                    downto condition_index * G_SAMPLE_W),
                                width_stage
                            ),
                            token_width_r(
                                width_stage - 1,
                                C_COND_BASE + condition_index
                            )
                        );
                        rise_width_r(
                            width_stage,
                            C_COND_BASE + condition_index
                        ) <= rise_width_r(
                            width_stage - 1,
                            C_COND_BASE + condition_index
                        ) or rise_masked_slice(
                            probe_pipe_r(width_stage - 1),
                            previous_pipe_r(width_stage - 1),
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            width_stage
                        );
                        fall_width_r(
                            width_stage,
                            C_COND_BASE + condition_index
                        ) <= fall_width_r(
                            width_stage - 1,
                            C_COND_BASE + condition_index
                        ) or fall_masked_slice(
                            probe_pipe_r(width_stage - 1),
                            previous_pipe_r(width_stage - 1),
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            width_stage
                        );
                    end loop;

                    for sequence_index in 0 to G_TRIG_STAGES - 1 loop
                        token_width_r(
                            width_stage,
                            C_SEQ_BASE + sequence_index
                        ) <= cmp_combine(
                            cmp_masked_slice(
                                probe_pipe_r(width_stage - 1),
                                seq_value_r_flat(
                                    sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                    downto sequence_index * G_SAMPLE_W),
                                seq_mask_r_flat(
                                    sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                    downto sequence_index * G_SAMPLE_W),
                                width_stage
                            ),
                            token_width_r(
                                width_stage - 1,
                                C_SEQ_BASE + sequence_index
                            )
                        );
                        rise_width_r(
                            width_stage,
                            C_SEQ_BASE + sequence_index
                        ) <= '0';
                        fall_width_r(
                            width_stage,
                            C_SEQ_BASE + sequence_index
                        ) <= '0';
                    end loop;
                end loop;
            end if;
        end if;
    end process;

    legacy_width_match <= token_matches(
        token_width_r(C_WIDTH_STAGES - 1, 0),
        rise_width_r(C_WIDTH_STAGES - 1, 0),
        fall_width_r(C_WIDTH_STAGES - 1, 0),
        trig_op
    );

    g_condition_results : for condition_index in 0 to G_TRIG_CONDS - 1 generate
        cond_width_match(condition_index) <= token_matches(
            token_width_r(
                C_WIDTH_STAGES - 1,
                C_COND_BASE + condition_index
            ),
            rise_width_r(
                C_WIDTH_STAGES - 1,
                C_COND_BASE + condition_index
            ),
            fall_width_r(
                C_WIDTH_STAGES - 1,
                C_COND_BASE + condition_index
            ),
            to_integer(unsigned(
                cond_ops_r(condition_index * 4 + 3 downto condition_index * 4)
            ))
        );
    end generate;

    g_sequence_results : for sequence_index in 0 to G_TRIG_STAGES - 1 generate
        seq_width_match(sequence_index) <= token_width_r(
            C_WIDTH_STAGES - 1,
            C_SEQ_BASE + sequence_index
        ).eq;
    end generate;

    g_no_condition_reduction : if C_REDUCE_STAGES = 0 generate
        legacy_final_match <= legacy_width_match;
        array_final_match <= cond_width_match(0) and cond_valid_r(0);
        seq_final_match <= seq_width_match;
        pipeline_final_valid <= valid_width_r(C_WIDTH_STAGES - 1);
        pipeline_final_ext <= ext_width_r(C_WIDTH_STAGES - 1);
        pipeline_final_ptr <= pointer_width_r(C_WIDTH_STAGES - 1);
    end generate;

    g_condition_reduction : if C_REDUCE_STAGES > 0 generate
    begin
        process (sample_clk, sample_rst)
            variable condition_leaves :
                std_logic_vector(C_REDUCE_WIDTH - 1 downto 0);
        begin
            if sample_rst = '1' then
                condition_reduce_r <= (others => (others => '1'));
                legacy_reduce_r <= (others => '0');
                seq_reduce_r <= (others => (others => '0'));
                valid_reduce_r <= (others => '0');
                ext_reduce_r <= (others => '0');
                pointer_reduce_r <= (others => (others => '0'));
            elsif rising_edge(sample_clk) then
                if reset_pulse = '1' or arm_pulse = '1' then
                    condition_reduce_r <= (others => (others => '1'));
                    legacy_reduce_r <= (others => '0');
                    seq_reduce_r <= (others => (others => '0'));
                    valid_reduce_r <= (others => '0');
                    ext_reduce_r <= (others => '0');
                    pointer_reduce_r <= (others => (others => '0'));
                else
                    condition_leaves := (others => '1');
                    for condition_index in 0 to G_TRIG_CONDS - 1 loop
                        condition_leaves(condition_index) :=
                            cond_width_match(condition_index) or
                            not cond_valid_r(condition_index);
                    end loop;
                    for node_index in 0 to C_REDUCE_WIDTH / 2 - 1 loop
                        condition_reduce_r(0)(node_index) <=
                            condition_leaves(2 * node_index) and
                            condition_leaves(2 * node_index + 1);
                    end loop;
                    for reduce_stage in 1 to C_REDUCE_STAGES - 1 loop
                        for node_index in 0 to
                            C_REDUCE_WIDTH / (2 ** (reduce_stage + 1)) - 1 loop
                            condition_reduce_r(reduce_stage)(node_index) <=
                                condition_reduce_r(reduce_stage - 1)(
                                    2 * node_index
                                ) and condition_reduce_r(reduce_stage - 1)(
                                    2 * node_index + 1
                                );
                        end loop;
                    end loop;

                    legacy_reduce_r(0) <= legacy_width_match;
                    seq_reduce_r(0) <= seq_width_match;
                    valid_reduce_r(0) <= valid_width_r(C_WIDTH_STAGES - 1);
                    ext_reduce_r(0) <= ext_width_r(C_WIDTH_STAGES - 1);
                    pointer_reduce_r(0) <= pointer_width_r(C_WIDTH_STAGES - 1);
                    for reduce_stage in 1 to C_REDUCE_STAGES - 1 loop
                        legacy_reduce_r(reduce_stage) <=
                            legacy_reduce_r(reduce_stage - 1);
                        seq_reduce_r(reduce_stage) <=
                            seq_reduce_r(reduce_stage - 1);
                        valid_reduce_r(reduce_stage) <=
                            valid_reduce_r(reduce_stage - 1);
                        ext_reduce_r(reduce_stage) <=
                            ext_reduce_r(reduce_stage - 1);
                        pointer_reduce_r(reduce_stage) <=
                            pointer_reduce_r(reduce_stage - 1);
                    end loop;
                end if;
            end if;
        end process;

        legacy_final_match <= legacy_reduce_r(C_REDUCE_STAGES - 1);
        array_final_match <=
            condition_reduce_r(C_REDUCE_STAGES - 1)(0)
            when cond_valid_r /= C_CONDS_NONE else '0';
        seq_final_match <= seq_reduce_r(C_REDUCE_STAGES - 1);
        pipeline_final_valid <= valid_reduce_r(C_REDUCE_STAGES - 1);
        pipeline_final_ext <= ext_reduce_r(C_REDUCE_STAGES - 1);
        pipeline_final_ptr <= pointer_reduce_r(C_REDUCE_STAGES - 1);
    end generate;

    trigger_hit <= seq_final_fire when seq_enable_r = '1'
              else array_final_match when array_enable_r = '1'
              else legacy_final_match;

    effective_internal <=
        (trigger_hit and pipeline_final_ext)
            when (ext_enable_r = '1' and ext_and_r = '1')
        else (trigger_hit or pipeline_final_ext) when ext_enable_r = '1'
        else trigger_hit;

    local_fire_pipe <= pipeline_final_valid and effective_internal;
    local_fire_ptr <= pipeline_final_ptr;

    -- v0.3 decimation tick: '1' every (decim_ratio + 1) cycles.
    -- decim_ratio = 0 → tick always high (no decimation, store every
    -- cycle — matches v0.1/v0.2 behavior).
    decim_tick <= '1' when decim_count_r = 0 else '0';

    seq_final_fire <= '1' when (
        seq_enable_r = '1' and
        pipeline_final_valid = '1' and
        seq_state_r = to_unsigned(G_TRIG_STAGES - 1, C_SEQ_STATE_W) and
        seq_final_match(G_TRIG_STAGES - 1) = '1' and
        seq_counter_view(G_TRIG_STAGES - 1) + 1
            >= seq_count_target_view(G_TRIG_STAGES - 1)
    ) else '0';

    -- ── Status outputs ───────────────────────────────────────────
    armed         <= armed_r;
    triggered     <= triggered_r;
    done          <= done_r;
    overflow      <= overflow_r;
    trigger_out   <= trigger_out_r;
    wr_ptr_out    <= std_logic_vector(wr_ptr_r);
    trig_ptr_out  <= std_logic_vector(trig_ptr_r);
    start_ptr_out <= std_logic_vector(start_ptr_r);

    -- ── DPRAM drive — sliding-window write enable. Note: NOT gated
    -- by `armed_r`. This is the architectural fix vs fcapz.
    -- v0.3: also gated by decim_tick so only every (decim_ratio+1)
    -- sample is stored. With decim_ratio=0 the tick is always 1 and
    -- behavior matches v0.1/v0.2 exactly. ───────────────────────
    dpram_we   <= '1' when (done_r = '0' and decim_tick = '1') else '0';
    dpram_addr <= std_logic_vector(wr_ptr_r);
    dpram_din  <= probe_in;

    -- ── Capture FSM ──────────────────────────────────────────────
    process (sample_clk, sample_rst)
    begin
        if sample_rst = '1' then
            armed_r        <= '0';
            triggered_r    <= '0';
            done_r         <= '0';
            overflow_r     <= '0';
            wr_ptr_r       <= (others => '0');
            trig_ptr_r     <= (others => '0');
            start_ptr_r    <= (others => '0');
            post_count_r   <= (others => '0');
            pretrig_len_r  <= (others => '0');
            posttrig_len_r <= (others => '0');
            trig_value_r   <= (others => '0');
            trig_mask_r    <= (others => '0');
            trig_mode_r    <= (others => '0');
            probe_prev_r   <= (others => '0');
            decim_ratio_r  <= (others => '0');
            decim_count_r  <= (others => '0');
            seq_enable_r   <= '0';
            seq_state_r    <= (others => '0');
            seq_value_r_flat        <= (others => '0');
            seq_mask_r_flat         <= (others => '0');
            seq_count_target_r_flat <= (others => '0');
            seq_counter_r_flat      <= (others => '0');
            array_enable_r <= '0';
            cond_values_r  <= (others => '0');
            cond_masks_r   <= (others => '0');
            cond_ops_r     <= (others => '0');
            cond_valid_r   <= (others => '0');
            ext_enable_r   <= '0';
            ext_and_r      <= '0';
            ext_trig_r     <= '0';
            trigger_out_r  <= '0';

        elsif rising_edge(sample_clk) then

            -- Default: trigger_out is a 1-cycle pulse.
            trigger_out_r <= '0';

            -- Previous-sample register for edge detection (RTL-P3.644).
            -- Updated every cycle so the rising/falling comparator always
            -- sees sample(N-1) alongside the current sample(N).
            probe_prev_r <= probe_in;

            -- External board-pin: register every cycle (RTL-P3.266). The pin
            -- is already sample_clk-synced in rr_rea_top; this is the local
            -- pipeline flop so the fold above sees a clean registered level.
            ext_trig_r <= ext_trigger_in;

            -- ── Free-running write pointer ─────────────────────
            -- REA-REQ-100/101: wr_ptr advances every cycle while
            -- !done, regardless of armed state. arm_pulse does NOT
            -- reset wr_ptr — pre-arm context is preserved.
            -- v0.3: also gated by decim_tick so wr_ptr only advances
            -- on stored samples (one per decim_ratio+1 cycles).
            if done_r = '0' and decim_tick = '1' then
                wr_ptr_r <= wr_ptr_r + 1;
            end if;

            -- ── v0.3 decimation counter ────────────────────────
            -- Down-counter that wraps at decim_ratio. When the counter
            -- hits 0, decim_tick fires for one cycle (storing this
            -- sample), then the counter reloads to decim_ratio.
            -- arm_pulse resets the counter so each capture session
            -- starts on a clean tick boundary.
            if done_r = '0' then
                if decim_count_r = 0 then
                    decim_count_r <= decim_ratio_r;
                else
                    decim_count_r <= decim_count_r - 1;
                end if;
            end if;

            -- ── reset_pulse: hard reset of capture state ───────
            if reset_pulse = '1' then
                armed_r       <= '0';
                triggered_r   <= '0';
                done_r        <= '0';
                overflow_r    <= '0';
                post_count_r  <= (others => '0');
                trigger_out_r <= '0';
                -- NOTE: wr_ptr_r is NOT reset on reset_pulse for v0.1
                -- — keeping the buffer state alive across soft resets
                -- is consistent with sliding-window semantics. Hard
                -- buffer-clearing only happens via sample_rst.
            end if;

            -- ── arm_pulse: enable trigger watching ─────────────
            -- Latches config, but does NOT reset wr_ptr_r.
            if arm_pulse = '1' then
                armed_r        <= '1';
                triggered_r    <= '0';
                done_r         <= '0';
                post_count_r   <= (others => '0');
                pretrig_len_r  <= unsigned(pretrig_len_in);
                posttrig_len_r <= unsigned(posttrig_len_in);
                trig_value_r   <= trig_value_in;
                trig_mask_r    <= trig_mask_in;
                trig_mode_r    <= trig_mode_in;
                decim_ratio_r  <= unsigned(decim_ratio_in);
                -- REA-REQ-606: arm_pulse resets seq_state to 0 and
                -- clears all counters; latches the per-stage config.
                seq_enable_r   <= seq_enable_in;
                seq_state_r    <= (others => '0');
                seq_value_r_flat        <= seq_values_in;
                seq_mask_r_flat         <= seq_masks_in;
                seq_count_target_r_flat <= seq_counts_in;
                seq_counter_r_flat      <= (others => '0');
                -- RTL-P3.647: latch the comparator-array config on arm too.
                array_enable_r <= array_enable_in;
                cond_values_r  <= cond_values_in;
                cond_masks_r   <= cond_masks_in;
                cond_ops_r     <= cond_ops_in;
                cond_valid_r   <= cond_valid_in;
                -- RTL-P3.266: latch external-trigger enable + combine mode on
                -- arm (quasi-static, like the other enables).
                ext_enable_r   <= ext_enable_in;
                ext_and_r      <= ext_and_in;
                -- Load count to 0 so the FIRST cycle after arm ticks
                -- (stores) — and subsequent ticks happen every
                -- (decim_ratio + 1) cycles. With decim_ratio=0 the
                -- counter reloads to 0 every cycle → tick every
                -- cycle (no decimation, matches v0.1/v0.2).
                decim_count_r  <= (others => '0');
                -- Overflow check: window doesn't fit in DEPTH.
                if (unsigned('0' & pretrig_len_in) +
                    unsigned('0' & posttrig_len_in)) >= G_DEPTH then
                    overflow_r <= '1';
                else
                    overflow_r <= '0';
                end if;
            end if;

            -- ── Sequencer state machine (REA-REQ-601..605) ─────
            -- Only advances when the CURRENT stage matches.
            -- Out-of-order matches are ignored (REA-REQ-605).
            -- Non-final-stage matches advance seq_state but do NOT
            -- fire triggered_r (REA-REQ-604) — the trigger-hit
            -- selector above gates the final-stage fire onto
            -- triggered_r via seq_final_fire.
            if seq_enable_r = '1' and armed_r = '1'
               and triggered_r = '0' and done_r = '0'
               and pipeline_final_valid = '1' then
                for k in 0 to G_TRIG_STAGES - 1 loop
                    if seq_state_r = to_unsigned(k, C_SEQ_STATE_W)
                       and seq_final_match(k) = '1' then
                        if seq_counter_view(k) + 1
                           >= seq_count_target_view(k) then
                            -- Reached the count target on this match.
                            -- Final stage → drive seq_final_fire (the
                            -- combinational signal feeding trigger_hit
                            -- which the trigger-detect block below
                            -- still gates onto triggered_r/trig_ptr_r).
                            -- Non-final stage → just advance.
                            if k = G_TRIG_STAGES - 1 then
                                null;  -- final fire handled below
                            else
                                seq_state_r <=
                                    seq_state_r + 1;
                                -- Reset stage K's counter slice in
                                -- the flat vector.
                                seq_counter_r_flat(
                                    k * 16 + 15 downto k * 16
                                ) <= (others => '0');
                            end if;
                        else
                            seq_counter_r_flat(
                                k * 16 + 15 downto k * 16
                            ) <= std_logic_vector(seq_counter_view(k) + 1);
                        end if;
                    end if;
                end loop;
            end if;

            -- ── Trigger detection ──────────────────────────────
            -- Fires only when armed and not yet triggered.
            -- REA-REQ-400/401: an external trigger_in pulse fires
            -- the capture exactly like a local hit, but does NOT
            -- drive trigger_out (otherwise N coupled REA cores
            -- would ping-pong each other forever).
            -- REA-REQ-602: in seq_enable mode, trigger_hit is the
            -- final-stage match path (seq_final_fire).
            if armed_r = '1' and triggered_r = '0' and done_r = '0' then
                if local_fire_pipe = '1' or trigger_in = '1' then
                    triggered_r <= '1';
                    if local_fire_pipe = '1' then
                        trig_ptr_r <= local_fire_ptr;
                    else
                        trig_ptr_r <= wr_ptr_r;
                    end if;
                    if local_fire_pipe = '1' then
                        -- LOCAL fire only (drives trig_xbar). In ext-AND mode
                        -- this means "our condition AND the pin both held",
                        -- so coupled cores see the true local event, not a
                        -- premature comparator-only hit. RTL-P3.266.
                        trigger_out_r <= '1';
                    end if;
                end if;
            end if;

            -- ── Post-trigger countdown ─────────────────────────
            -- v0.3: counts STORED samples only (decim_tick gate),
            -- so the post-trigger window is `posttrig_len` cells
            -- regardless of decimation ratio.
            if armed_r = '1' and triggered_r = '1' and done_r = '0'
               and decim_tick = '1' then
                if post_count_r >= posttrig_len_r then
                    -- Done capturing the post-trigger window.
                    -- REA-REQ-104: start_ptr <= trig_ptr - pretrig_len
                    -- (mod DEPTH — natural wrap on PTR_W-bit subtract).
                    done_r      <= '1';
                    armed_r     <= '0';
                    start_ptr_r <= trig_ptr_r - pretrig_len_r;
                else
                    post_count_r <= post_count_r + 1;
                end if;
            end if;

        end if;
    end process;

end architecture;
