-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_capture_fsm — sliding-window capture state machine.
--
-- THIS IS WHERE WE EXPLICITLY DIVERGE FROM the reference ELA design.
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
-- The naive `mem_we_a = armed && !done && store_enable` leaves uninit
-- BRAM cells in the captured window when the trigger fires before
-- pretrig_len cycles have elapsed since arm. We do not ship that.
--
-- The store gate is `!done && decim_tick && qual_ok` (plus the post-trigger
-- window stop). v0.3 added the decimation tick; REA-P2.7 (v0.11) adds the
-- storage qualifier `qual_ok`, elaborated only when G_QUAL_CONDS > 0 and
-- constant '1' while QUAL_MODE[0] (latched on arm) is 0, so qualification
-- off is the v0.9 analyzer bit for bit (REA-REQ-950).
--
-- See requirements.yml REA-REQ-100..106 and REA-REQ-950..959 for the test
-- contract.

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
        G_TRIG_CONDS   : positive := 4;  -- v0.5 comparator-array slots (P3.647)
        G_QUAL_CONDS   : natural  := 0   -- REA-P2.7 storage-qualifier slots
    );
    port (
        sample_clk_i  : in  std_logic;
        sample_rst_i  : in  std_logic;

        -- ── Probe input (sync'd to sample_clk by the caller) ─────
        probe_i    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Control pulses (sync'd to sample_clk by rr_rea_cdc) ──
        arm_pulse_i   : in  std_logic;   -- 1 cycle wide
        reset_pulse_i : in  std_logic;   -- 1 cycle wide; clears state

        -- ── External trigger input (REA-REQ-400) ─────────────────
        -- 1-cycle pulse on sample_clk from the cross-domain trigger
        -- crossbar (rr_rea_trig_xbar) — when armed, fires the
        -- capture as if the local comparator hit. Does NOT drive
        -- trigger_out (that would create a ping-pong loop with
        -- other REA instances on the bus). Tied low when the
        -- crossbar isn't connected.
        trigger_i  : in  std_logic := '0';

        -- ── Latched config (sample_clk domain) ───────────────────
        pretrig_len_i  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_i : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_i   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_i    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- Comparator op for the legacy single-comparator path (RTL-P3.644/
        -- 645/646): bits[7:4] of TRIG_MODE = C_TRIG_OP_{EQ,NE,LT,GT,RISE,FALL}.
        -- Default 0 → EQ → historical masked-equality behaviour (back-compat).
        trig_mode_i    : in  std_logic_vector(7 downto 0) := (others => '0');
        -- v0.3 decimation: capture every (decim_ratio + 1) samples.
        -- Tied 0 disables decimation (every sample stored). Latched
        -- on arm_pulse like the other config.
        decim_ratio_i  : in  std_logic_vector(23 downto 0)
                              := (others => '0');

        -- ── v0.3 multi-stage sequencer (REA-REQ-600..607) ────────
        -- seq_enable_in selects between the legacy single-comparator
        -- path (trig_value_in / trig_mask_in) and the per-stage
        -- sequencer below. Tied 0 → legacy path (REA-REQ-600).
        seq_enable_i     : in  std_logic := '0';

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
        seq_values_i     : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_masks_i      : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_counts_i     : in  std_logic_vector(
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
        array_enable_i   : in  std_logic := '0';
        cond_values_i    : in  std_logic_vector(
            G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        cond_masks_i     : in  std_logic_vector(
            G_TRIG_CONDS * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        cond_ops_i       : in  std_logic_vector(
            G_TRIG_CONDS * 4 - 1 downto 0)
                              := (others => '0');
        cond_valid_i     : in  std_logic_vector(G_TRIG_CONDS - 1 downto 0)
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
        ext_trigger_i    : in  std_logic := '0';
        ext_enable_i     : in  std_logic := '0';
        ext_and_i        : in  std_logic := '0';

        -- ── REA-P2.7 storage qualifier (REA-REQ-951..953, 959) ───
        -- Latched on arm like the trigger config. qual_enable_i='0' (the
        -- default, and QUAL_MODE's reset value) stores every sample. Slot k
        -- is value/mask/op over the full probe, expanded by the regbank from
        -- the compact COND_CFG encoding; ops EQ/NE/RISE/FALL, anything else
        -- never qualifies. qual_or_i selects OR (else AND) over valid slots.
        -- Sized for max(1, G_QUAL_CONDS) slots; ignored when it is 0.
        qual_enable_i    : in  std_logic := '0';
        qual_or_i        : in  std_logic := '0';
        qual_values_i    : in  std_logic_vector(
            max_nat(1, G_QUAL_CONDS) * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        qual_masks_i     : in  std_logic_vector(
            max_nat(1, G_QUAL_CONDS) * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        qual_ops_i       : in  std_logic_vector(
            max_nat(1, G_QUAL_CONDS) * 4 - 1 downto 0)
                              := (others => '0');
        qual_valid_i     : in  std_logic_vector(
            max_nat(1, G_QUAL_CONDS) - 1 downto 0)
                              := (others => '0');

        -- ── Status flags (combinational from registers) ──────────
        armed_o       : out std_logic;
        triggered_o   : out std_logic;
        done_o        : out std_logic;
        overflow_o    : out std_logic;
        trigger_o : out std_logic;   -- 1-cycle pulse on local fire

        -- ── DPRAM port-A drive ───────────────────────────────────
        dpram_we_o    : out std_logic;
        dpram_addr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dpram_din_o   : out std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Pointer outputs (regbank readback) ───────────────────
        wr_ptr_o    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_ptr_o  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        start_ptr_o : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);

        -- RTL-T1.16: how many PRE-TRIGGER cells in the captured window were
        -- actually written after this arm, i.e. are contiguous with THIS
        -- trigger. The sliding-window write enable stops at done_o so the
        -- window can be read back (see A1 — that gate is load-bearing and must
        -- NOT be widened), which means the buffer is frozen between captures.
        -- If the trigger fires within pretrig_len samples of the arm, the
        -- remaining pre-trigger cells still hold the PREVIOUS capture's tail —
        -- stale but entirely plausible data, which is worse than uninitialised
        -- cells because it reads as real context. The host trims to this count.
        pretrig_valid_o : out std_logic_vector(clog2(G_DEPTH) downto 0)
    );
end entity;

architecture rtl of rr_rea_capture_fsm is

    constant C_PTR_W : positive := clog2(G_DEPTH);
    constant C_WIDTH_STAGES : positive :=
        (G_SAMPLE_W + C_SLICE_W - 1) / C_SLICE_W;
    constant C_REDUCE_STAGES : natural :=
        clog2(G_TRIG_CONDS);
    constant C_PIPE_STAGES : positive :=
        rea_trig_latency(G_SAMPLE_W, G_TRIG_CONDS);  -- = width + reduce stages
    constant C_WIDTH_TREE_STAGES : positive :=
        max_nat(1, (clog2(C_WIDTH_STAGES) + 1) / 2);
    constant C_WIDTH_TREE_NODES : positive :=
        max_nat(1, (C_WIDTH_STAGES + 3) / 4);
    constant C_WIDTH_ALIGN_STAGES : natural :=
        C_WIDTH_STAGES - C_WIDTH_TREE_STAGES;
    constant C_WIDTH_ALIGN_STORAGE : positive :=
        max_nat(1, C_WIDTH_ALIGN_STAGES);
    constant C_NUM_COMPARATORS : positive :=
        1 + G_TRIG_CONDS + G_TRIG_STAGES;
    constant C_COND_BASE : positive := 1;
    constant C_SEQ_BASE : positive := C_COND_BASE + G_TRIG_CONDS;
    constant C_REDUCE_WIDTH : positive := 2 ** C_REDUCE_STAGES;
    constant C_REDUCE_STORAGE : positive := max_nat(1, C_REDUCE_STAGES);
    constant C_CMP_EQUAL : t_cmp_token := (eq => '1', gt => '0', lt => '0');

    type t_cmp_group is array (0 to 3) of t_cmp_token;

    function cmp_group_combine(
        token_group : t_cmp_group
    ) return t_cmp_token is
    begin
        return cmp_combine(
            cmp_combine(token_group(3), token_group(2)),
            cmp_combine(token_group(1), token_group(0))
        );
    end function;

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

    function cmp_masked_group(
        probe_value : std_logic_vector;
        match_value : std_logic_vector;
        match_mask  : std_logic_vector;
        group_index : natural
    ) return t_cmp_token is
        variable token_group : t_cmp_group := (others => C_CMP_EQUAL);
        variable slice_index : natural;
    begin
        for group_offset in 0 to 3 loop
            slice_index := group_index * 4 + group_offset;
            if slice_index < C_WIDTH_STAGES then
                token_group(group_offset) := cmp_masked_slice(
                    probe_value,
                    match_value,
                    match_mask,
                    slice_index
                );
            end if;
        end loop;
        return cmp_group_combine(token_group);
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

    function rise_masked_group(
        current_value  : std_logic_vector;
        previous_value : std_logic_vector;
        match_mask     : std_logic_vector;
        group_index    : natural
    ) return std_logic is
        variable result : std_logic := '0';
        variable slice_index : natural;
    begin
        for group_offset in 0 to 3 loop
            slice_index := group_index * 4 + group_offset;
            if slice_index < C_WIDTH_STAGES then
                result := result or rise_masked_slice(
                    current_value,
                    previous_value,
                    match_mask,
                    slice_index
                );
            end if;
        end loop;
        return result;
    end function;

    function fall_masked_group(
        current_value  : std_logic_vector;
        previous_value : std_logic_vector;
        match_mask     : std_logic_vector;
        group_index    : natural
    ) return std_logic is
        variable result : std_logic := '0';
        variable slice_index : natural;
    begin
        for group_offset in 0 to 3 loop
            slice_index := group_index * 4 + group_offset;
            if slice_index < C_WIDTH_STAGES then
                result := result or fall_masked_slice(
                    current_value,
                    previous_value,
                    match_mask,
                    slice_index
                );
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

    type t_ptr_pipe is array (0 to C_WIDTH_STAGES - 1)
        of unsigned(C_PTR_W - 1 downto 0);
    type t_token_tree is array (
        0 to C_WIDTH_TREE_STAGES - 1,
        0 to C_WIDTH_TREE_NODES - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of t_cmp_token;
    type t_edge_token is record
        rise : std_logic;
        fall : std_logic;
    end record;
    type t_edge_tree is array (
        0 to C_WIDTH_TREE_STAGES - 1,
        0 to C_WIDTH_TREE_NODES - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of t_edge_token;
    type t_token_align_pipe is array (
        0 to C_WIDTH_ALIGN_STORAGE - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of t_cmp_token;
    type t_edge_align_pipe is array (
        0 to C_WIDTH_ALIGN_STORAGE - 1,
        0 to C_NUM_COMPARATORS - 1
    ) of t_edge_token;
    type t_token_result is array (0 to C_NUM_COMPARATORS - 1)
        of t_cmp_token;
    type t_edge_result is array (0 to C_NUM_COMPARATORS - 1)
        of t_edge_token;
    type t_condition_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic_vector(C_REDUCE_WIDTH - 1 downto 0);
    type t_bit_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic;
    type t_seq_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of std_logic_vector(G_TRIG_STAGES - 1 downto 0);
    type t_ptr_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1)
        of unsigned(C_PTR_W - 1 downto 0);
    -- REA-T2.5: min(since_arm, PRETRIG) as of each sample, carried down the
    -- trigger pipeline beside that sample's pointer.
    subtype t_pv is unsigned(clog2(G_DEPTH) downto 0);
    type t_pv_pipe is array (0 to C_WIDTH_STAGES - 1) of t_pv;
    type t_pv_reduce_pipe is array (0 to C_REDUCE_STORAGE - 1) of t_pv;

    signal pointer_width_r : t_ptr_pipe := (others => (others => '0'));
    signal token_tree_r : t_token_tree :=
        (others => (others => (others => C_CMP_EQUAL)));
    signal edge_tree_r : t_edge_tree := (
        others => (others => (others => (rise => '0', fall => '0')))
    );
    signal token_align_r : t_token_align_pipe :=
        (others => (others => C_CMP_EQUAL));
    signal edge_align_r : t_edge_align_pipe := (
        others => (others => (rise => '0', fall => '0'))
    );
    signal token_width_result : t_token_result :=
        (others => C_CMP_EQUAL);
    signal edge_width_result : t_edge_result :=
        (others => (rise => '0', fall => '0'));
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
    signal pv_in              : t_pv;
    -- PRETRIG > DEPTH - 1 - C_PIPE_STAGES, registered (pretrig_len_r only
    -- changes on arm; no pipeline sample can fire in the arm cycle).
    signal pv_over_r          : std_logic := '0';
    signal pv_sat             : t_pv;
    signal pv_width_r         : t_pv_pipe := (others => (others => '0'));
    signal pv_reduce_r        : t_pv_reduce_pipe := (others => (others => '0'));
    signal pipeline_final_pv  : t_pv;

    signal armed_r       : std_logic := '0';
    signal triggered_r   : std_logic := '0';
    signal done_r        : std_logic := '0';
    signal overflow_r    : std_logic := '0';
    signal wr_ptr_r      : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal trig_ptr_r    : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    -- RTL-T1.16 pre-trigger contiguity accounting.
    signal since_arm_r     : unsigned(clog2(G_DEPTH) downto 0)
        := (others => '0');
    signal pretrig_valid_r : unsigned(clog2(G_DEPTH) downto 0)
        := (others => '0');
    signal start_ptr_r   : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    -- One bit wider than the pointer: with qualification on it counts written
    -- post-trigger cells up to POSTTRIG+1, which is DEPTH when POSTTRIG=DEPTH-1.
    -- Qualification off never exceeds POSTTRIG, so the extra bit stays 0.
    signal post_count_r  : unsigned(C_PTR_W downto 0) := (others => '0');
    -- REA-P2.11: post_full is a REGISTER kept in step with post_count_r, so
    -- the window-full compare no longer sits between post_count_r and the
    -- store strobe (post_count -> compare -> store_sample -> post_count adder
    -- was the Fmax limiter in every configuration). The compare threshold is
    -- latched at arm: POSTTRIG, or POSTTRIG + 1 when exact_count (see
    -- post_full's history below).
    signal post_full_r   : std_logic := '0';
    signal pf_thr_m1_r   : unsigned(C_PTR_W downto 0) := (others => '0');
    -- At the fire edge post_count loads the trigger-pipeline lag, at most
    -- C_PIPE_STAGES + 1, so only C_PF_K low bits take part in that compare.
    constant C_PF_K      : positive := clog2(C_PIPE_STAGES + 2);
    signal pf_thr_small_r : std_logic := '0';
    signal pf_thr_low_r  : unsigned(C_PF_K - 1 downto 0) := (others => '0');
    -- The post-trigger window is full: stop storing, then finish (REA-REQ-954).
    signal post_full     : std_logic;
    signal pretrig_len_r : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal posttrig_len_r: unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal decim_ratio_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_count_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_tick    : std_logic;
    signal store_sample  : std_logic;

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

    -- ── REA-P2.7 storage qualifier ──────────────────────────────
    constant C_QUAL_SLOTS : positive := max_nat(1, G_QUAL_CONDS);
    -- Enables/ops/valid are resettable control; the wide value/mask are
    -- validity-gated arm-time data (RTL-P2.897), like cond_values_r.
    signal qual_enable_r  : std_logic := '0';
    -- REA-T2.4: exact window arithmetic whenever a store can be skipped
    -- (qualification OR decimation). See the post_full comment.
    signal exact_count    : std_logic;
    signal qual_or_r      : std_logic := '0';
    signal qual_ops_r     : std_logic_vector(C_QUAL_SLOTS * 4 - 1 downto 0)
                               := (others => '0');
    signal qual_valid_r   : std_logic_vector(C_QUAL_SLOTS - 1 downto 0)
                               := (others => '0');
    signal qual_values_r  : std_logic_vector(C_QUAL_SLOTS * G_SAMPLE_W - 1 downto 0)
                               := (others => '0');
    signal qual_masks_r   : std_logic_vector(C_QUAL_SLOTS * G_SAMPLE_W - 1 downto 0)
                               := (others => '0');
    -- qual_ok: this cycle's sample may be stored. '1' whenever qualification
    -- is off. store_tick = decimation tick AND qualifier: the one strobe that
    -- writes, advances wr_ptr and counts the post-trigger window.
    signal qual_ok        : std_logic;
    signal store_tick     : std_logic;
    -- Stored samples between the triggering sample and now (REA-REQ-955).
    signal fire_lag       : unsigned(C_PTR_W - 1 downto 0);
    signal effective_internal : std_logic;

    -- RTL-P2.658(b): the trig value/mask are banked into ceil(W/32) 32-bit
    -- JTAG words (paged via TRIG_WORD_SEL), so probes wider than 32 bits are
    -- supported up to C_MAX_SAMPLE_W (1024 as of RTL-P2.876). Fail fast at
    -- elaboration past the ceiling rather than emit an opaque 'array index out
    -- of range' deep in synth.
    constant C_SAMPLE_W_OK : boolean := (G_SAMPLE_W <= C_MAX_SAMPLE_W);

    -- RTL-P2.895 — UN-IGNORABLE elaboration ceiling guard.
    -- The `assert ... severity failure` below is the FRIENDLY message, but a
    -- runtime assert is a WARNING to vendor synthesis (Quartus, Vivado): it
    -- prints, then ships undefined silicon anyway (field signature: any register
    -- read with bit0=1 returned 0xFFFFFFFF — RTL-P2.895). This constant makes the
    -- violation a STATIC RANGE ERROR instead: boolean'pos(over_ceiling) is 1 when
    -- G_SAMPLE_W > C_MAX_SAMPLE_W, and assigning 1 to a `range 0 to 0` subtype is
    -- an out-of-range constant that EVERY VHDL tool must reject AT ELABORATION —
    -- it cannot be downgraded to a warning. The build HALTS here; no over-ceiling
    -- silicon can be produced. In-ceiling the value is 0 (legal, zero cost).
    -- (A `std_logic_vector(0 to N*2-2)` "negative range" bomb does NOT work —
    -- 0 to -2 is a legal NULL range; the subtype-constraint bomb genuinely fails.
    -- Proven against nvc 1.19; Quartus/Vivado enforcement is the integrator-
    -- validation checkpoint for RTL-P2.895.)
    constant C_CEILING_GUARD : natural range 0 to 0 :=
        boolean'pos(G_SAMPLE_W > C_MAX_SAMPLE_W);

begin

    assert C_SAMPLE_W_OK
        report "rr_rea: G_SAMPLE_W exceeds C_MAX_SAMPLE_W (1024) - raise the "
             & "ceiling in rr_rea_pkg or cap the probe (RTL-P2.876/P2.895). "
             & "NB this friendly assert is a WARNING to vendor synth; the "
             & "C_CEILING_GUARD constant hard-fails elaboration in all tools."
        severity failure;

    -- ── Per-stage views (combinational slices of flat vectors) ──
    g_seq_views : for k in 0 to G_TRIG_STAGES - 1 generate
        seq_count_target_view(k) <= unsigned(seq_count_target_r_flat(
            k * 16 + 15 downto k * 16));
        seq_counter_view(k) <= unsigned(seq_counter_r_flat(
            k * 16 + 15 downto k * 16));
    end generate;

    trig_op <= to_integer(unsigned(trig_mode_r(7 downto 4)));

    process (sample_clk_i, sample_rst_i)
        variable token_group : t_cmp_group;
        variable rise_group : std_logic_vector(3 downto 0);
        variable fall_group : std_logic_vector(3 downto 0);
        variable child_index : natural;
    begin
        if sample_rst_i = '1' then
            pointer_width_r <= (others => (others => '0'));
            pv_width_r <= (others => (others => '0'));
            valid_width_r <= (others => '0');
            ext_width_r <= (others => '0');
        elsif rising_edge(sample_clk_i) then
            if reset_pulse_i = '1' or arm_pulse_i = '1' then
                valid_width_r <= (others => '0');
                ext_width_r <= (others => '0');
            else
                pointer_width_r(0) <= wr_ptr_r;
                pv_width_r(0) <= pv_in;
                valid_width_r(0) <= armed_r and not triggered_r and not done_r;
                ext_width_r(0) <= ext_trig_r;
                for width_stage in 1 to C_WIDTH_STAGES - 1 loop
                    pointer_width_r(width_stage) <=
                        pointer_width_r(width_stage - 1);
                    pv_width_r(width_stage) <= pv_width_r(width_stage - 1);
                    valid_width_r(width_stage) <= valid_width_r(width_stage - 1);
                    ext_width_r(width_stage) <= ext_width_r(width_stage - 1);
                end loop;

                for width_node in 0 to C_WIDTH_TREE_NODES - 1 loop
                    token_tree_r(0, width_node, 0) <= cmp_masked_group(
                        probe_i, trig_value_r, trig_mask_r, width_node);
                    edge_tree_r(0, width_node, 0).rise <= rise_masked_group(
                        probe_i, probe_prev_r, trig_mask_r, width_node);
                    edge_tree_r(0, width_node, 0).fall <= fall_masked_group(
                        probe_i, probe_prev_r, trig_mask_r, width_node);

                    for condition_index in 0 to G_TRIG_CONDS - 1 loop
                        token_tree_r(
                            0,
                            width_node,
                            C_COND_BASE + condition_index
                        ) <= cmp_masked_group(
                            probe_i,
                            cond_values_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            width_node
                        );
                        edge_tree_r(
                            0,
                            width_node,
                            C_COND_BASE + condition_index
                        ).rise <= rise_masked_group(
                            probe_i,
                            probe_prev_r,
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            width_node
                        );
                        edge_tree_r(
                            0,
                            width_node,
                            C_COND_BASE + condition_index
                        ).fall <= fall_masked_group(
                            probe_i,
                            probe_prev_r,
                            cond_masks_r(
                                condition_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto condition_index * G_SAMPLE_W),
                            width_node
                        );
                    end loop;

                    for sequence_index in 0 to G_TRIG_STAGES - 1 loop
                        token_tree_r(
                            0,
                            width_node,
                            C_SEQ_BASE + sequence_index
                        ) <= cmp_masked_group(
                            probe_i,
                            seq_value_r_flat(
                                sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto sequence_index * G_SAMPLE_W),
                            seq_mask_r_flat(
                                sequence_index * G_SAMPLE_W + G_SAMPLE_W - 1
                                downto sequence_index * G_SAMPLE_W),
                            width_node
                        );
                        edge_tree_r(
                            0,
                            width_node,
                            C_SEQ_BASE + sequence_index
                        ).rise <= '0';
                        edge_tree_r(
                            0,
                            width_node,
                            C_SEQ_BASE + sequence_index
                        ).fall <= '0';
                    end loop;
                end loop;

                for width_level in 1 to C_WIDTH_TREE_STAGES - 1 loop
                    for width_node in 0 to
                        (
                            C_WIDTH_STAGES + 4 ** (width_level + 1) - 1
                        ) / (4 ** (width_level + 1)) - 1 loop
                        for comparator_index in
                            0 to C_NUM_COMPARATORS - 1 loop
                            token_group := (others => C_CMP_EQUAL);
                            rise_group := (others => '0');
                            fall_group := (others => '0');
                            for child_offset in 0 to 3 loop
                                child_index := width_node * 4 + child_offset;
                                if child_index < (
                                    C_WIDTH_STAGES + 4 ** width_level - 1
                                ) / (4 ** width_level) then
                                    token_group(child_offset) := token_tree_r(
                                        width_level - 1,
                                        child_index,
                                        comparator_index
                                    );
                                    rise_group(child_offset) := edge_tree_r(
                                        width_level - 1,
                                        child_index,
                                        comparator_index
                                    ).rise;
                                    fall_group(child_offset) := edge_tree_r(
                                        width_level - 1,
                                        child_index,
                                        comparator_index
                                    ).fall;
                                end if;
                            end loop;
                            token_tree_r(
                                width_level,
                                width_node,
                                comparator_index
                            ) <= cmp_group_combine(token_group);
                            edge_tree_r(
                                width_level,
                                width_node,
                                comparator_index
                            ).rise <= rise_group(0) or rise_group(1)
                                or rise_group(2) or rise_group(3);
                            edge_tree_r(
                                width_level,
                                width_node,
                                comparator_index
                            ).fall <= fall_group(0) or fall_group(1)
                                or fall_group(2) or fall_group(3);
                        end loop;
                    end loop;
                end loop;

                if C_WIDTH_ALIGN_STAGES > 0 then
                    for comparator_index in
                        0 to C_NUM_COMPARATORS - 1 loop
                        token_align_r(0, comparator_index) <= token_tree_r(
                            C_WIDTH_TREE_STAGES - 1, 0, comparator_index);
                        edge_align_r(0, comparator_index) <= edge_tree_r(
                            C_WIDTH_TREE_STAGES - 1, 0, comparator_index);
                    end loop;
                    for width_stage in 1 to C_WIDTH_ALIGN_STAGES - 1 loop
                        for comparator_index in
                            0 to C_NUM_COMPARATORS - 1 loop
                            token_align_r(width_stage, comparator_index) <=
                                token_align_r(width_stage - 1, comparator_index);
                            edge_align_r(width_stage, comparator_index) <=
                                edge_align_r(
                                    width_stage - 1, comparator_index);
                        end loop;
                    end loop;
                end if;
            end if;
        end if;
    end process;

    g_width_tree_result : if C_WIDTH_ALIGN_STAGES = 0 generate
        g_tree_result_comparators :
        for comparator_index in 0 to C_NUM_COMPARATORS - 1 generate
            token_width_result(comparator_index) <= token_tree_r(
                C_WIDTH_TREE_STAGES - 1, 0, comparator_index);
            edge_width_result(comparator_index) <= edge_tree_r(
                C_WIDTH_TREE_STAGES - 1, 0, comparator_index);
        end generate;
    end generate;

    g_width_aligned_result : if C_WIDTH_ALIGN_STAGES > 0 generate
        g_aligned_result_comparators :
        for comparator_index in 0 to C_NUM_COMPARATORS - 1 generate
            token_width_result(comparator_index) <= token_align_r(
                C_WIDTH_ALIGN_STAGES - 1, comparator_index);
            edge_width_result(comparator_index) <= edge_align_r(
                C_WIDTH_ALIGN_STAGES - 1, comparator_index);
        end generate;
    end generate;

    legacy_width_match <= token_matches(
        token_width_result(0),
        edge_width_result(0).rise,
        edge_width_result(0).fall,
        trig_op
    );

    g_condition_results : for condition_index in 0 to G_TRIG_CONDS - 1 generate
        cond_width_match(condition_index) <= token_matches(
            token_width_result(C_COND_BASE + condition_index),
            edge_width_result(C_COND_BASE + condition_index).rise,
            edge_width_result(C_COND_BASE + condition_index).fall,
            to_integer(unsigned(
                cond_ops_r(condition_index * 4 + 3 downto condition_index * 4)
            ))
        );
    end generate;

    g_sequence_results : for sequence_index in 0 to G_TRIG_STAGES - 1 generate
        seq_width_match(sequence_index) <= token_width_result(
            C_SEQ_BASE + sequence_index).eq;
    end generate;

    g_no_condition_reduction : if C_REDUCE_STAGES = 0 generate
        legacy_final_match <= legacy_width_match;
        array_final_match <= cond_width_match(0) and cond_valid_r(0);
        seq_final_match <= seq_width_match;
        pipeline_final_valid <= valid_width_r(C_WIDTH_STAGES - 1);
        pipeline_final_ext <= ext_width_r(C_WIDTH_STAGES - 1);
        pipeline_final_ptr <= pointer_width_r(C_WIDTH_STAGES - 1);
        pipeline_final_pv <= pv_width_r(C_WIDTH_STAGES - 1);
    end generate;

    g_condition_reduction : if C_REDUCE_STAGES > 0 generate
    begin
        process (sample_clk_i, sample_rst_i)
            variable condition_leaves :
                std_logic_vector(C_REDUCE_WIDTH - 1 downto 0);
        begin
            if sample_rst_i = '1' then
                condition_reduce_r <= (others => (others => '1'));
                legacy_reduce_r <= (others => '0');
                seq_reduce_r <= (others => (others => '0'));
                valid_reduce_r <= (others => '0');
                ext_reduce_r <= (others => '0');
                pointer_reduce_r <= (others => (others => '0'));
                pv_reduce_r <= (others => (others => '0'));
            elsif rising_edge(sample_clk_i) then
                if reset_pulse_i = '1' or arm_pulse_i = '1' then
                    condition_reduce_r <= (others => (others => '1'));
                    legacy_reduce_r <= (others => '0');
                    seq_reduce_r <= (others => (others => '0'));
                    valid_reduce_r <= (others => '0');
                    ext_reduce_r <= (others => '0');
                    pointer_reduce_r <= (others => (others => '0'));
                    pv_reduce_r <= (others => (others => '0'));
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
                    pv_reduce_r(0) <= pv_width_r(C_WIDTH_STAGES - 1);
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
                        pv_reduce_r(reduce_stage) <=
                            pv_reduce_r(reduce_stage - 1);
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
        pipeline_final_pv <= pv_reduce_r(C_REDUCE_STAGES - 1);
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

    -- ── REA-P2.7 storage qualifier (REA-REQ-951..953) ────────────
    -- Combinational on purpose: it must decide in the cycle the sample is
    -- written, so it is equality/edge only (no magnitude comparator —
    -- REA-REQ-953) and shallow: an AND/OR reduction over the probe.
    g_no_qual : if G_QUAL_CONDS = 0 generate
        qual_ok <= '1';
    end generate;

    g_qual : if G_QUAL_CONDS > 0 generate
        process (all)
            variable v_mask    : std_logic_vector(G_SAMPLE_W - 1 downto 0);
            variable v_value   : std_logic_vector(G_SAMPLE_W - 1 downto 0);
            variable v_eq      : std_logic;
            variable v_rise    : std_logic;
            variable v_fall    : std_logic;
            variable v_hit     : std_logic;
            variable v_any     : std_logic;   -- OR over valid slots
            variable v_all     : std_logic;   -- AND over valid slots
            variable v_some    : std_logic;   -- at least one valid slot
        begin
            v_any  := '0';
            v_all  := '1';
            v_some := '0';
            for slot_index in 0 to G_QUAL_CONDS - 1 loop
                v_mask := qual_masks_r(
                    slot_index * G_SAMPLE_W + G_SAMPLE_W - 1
                    downto slot_index * G_SAMPLE_W);
                v_value := qual_values_r(
                    slot_index * G_SAMPLE_W + G_SAMPLE_W - 1
                    downto slot_index * G_SAMPLE_W);
                v_eq := '1';
                if ((probe_i xor v_value) and v_mask) /= (v_mask'range => '0') then
                    v_eq := '0';
                end if;
                v_rise := '0';
                if (probe_i and not probe_prev_r and v_mask)
                   /= (v_mask'range => '0') then
                    v_rise := '1';
                end if;
                v_fall := '0';
                if (probe_prev_r and not probe_i and v_mask)
                   /= (v_mask'range => '0') then
                    v_fall := '1';
                end if;
                case to_integer(unsigned(qual_ops_r(
                        (slot_index + 1) * C_TRIG_OP_WIDTH - 1
                        downto slot_index * C_TRIG_OP_WIDTH))) is
                    when C_TRIG_OP_EQ   => v_hit := v_eq;
                    when C_TRIG_OP_NE   => v_hit := not v_eq;
                    when C_TRIG_OP_RISE => v_hit := v_rise;
                    when C_TRIG_OP_FALL => v_hit := v_fall;
                    when others         => v_hit := '0';
                end case;
                if qual_valid_r(slot_index) = '1' then
                    v_some := '1';
                    v_any  := v_any or v_hit;
                    v_all  := v_all and v_hit;
                end if;
            end loop;
            if qual_enable_r = '0' or v_some = '0' then
                qual_ok <= '1';
            elsif qual_or_r = '1' then
                qual_ok <= v_any;
            else
                qual_ok <= v_all;
            end if;
        end process;
    end generate;

    store_tick <= decim_tick and qual_ok;

    -- Every-cycle storing (no decimation, no qualification — the v0.9
    -- arithmetic): post_count lags the written cells by one because the fire
    -- edge always stores, so the window is full at POSTTRIG. Whenever a store
    -- can be skipped (qualification, REA-REQ-954/955, or DECIM > 0,
    -- REA-T2.4 / REA-REQ-960) the fire edge may fall in a gap, so post_count
    -- is the exact number of cells written from the trigger position on and
    -- the window (trigger cell + POSTTRIG) is full at POSTTRIG + 1. Using the
    -- lagged form under decimation stopped the window one cell short in most
    -- trigger phases and left a stale cell from an earlier capture last.
    exact_count <= '1' when qual_enable_r = '1' or decim_ratio_r /= 0
                   else '0';

    -- Window full: post_count_r >= POSTTRIG (post_count_r > POSTTRIG when
    -- exact_count). Held in post_full_r, updated wherever post_count_r is.
    post_full <= post_full_r;

    -- pragma translate_off
    -- REA-P2.11 equivalence guard (simulation only): whenever post_full is
    -- consulted, the register must equal the combinational definition it
    -- replaced. Every sim that reaches post-trigger checks the retiming.
    p_post_full_equiv : process (sample_clk_i)
        variable v_ref : std_logic;
    begin
        if rising_edge(sample_clk_i) then
            if sample_rst_i = '0' and armed_r = '1' and triggered_r = '1'
               and done_r = '0' then
                if (exact_count = '0' and post_count_r >= resize(
                        posttrig_len_r, post_count_r'length))
                   or (exact_count = '1' and post_count_r > resize(
                        posttrig_len_r, post_count_r'length)) then
                    v_ref := '1';
                else
                    v_ref := '0';
                end if;
                assert post_full_r = v_ref
                    report "REA-P2.11: registered post_full diverged from "
                           & "post_count vs POSTTRIG"
                    severity failure;
            end if;
        end if;
    end process;
    -- pragma translate_on

    fire_lag <= wr_ptr_r - local_fire_ptr when local_fire_pipe = '1'
                else (others => '0');

    -- REA-T2.5: PRETRIG_VALID of a pipeline-detected trigger is the number of
    -- samples stored after the arm and before the TRIGGERING sample, capped at
    -- PRETRIG. That is since_arm as it stood when the sample entered the
    -- trigger pipeline: every store between entry and fire bumps since_arm and
    -- wr_ptr alike, so it equals the old since_arm - fire_lag, except that
    -- since_arm saturates at DEPTH, where the subtraction undercounted (DEPTH
    -- - lag < PRETRIG). Capping here and copying the carried value at the fire
    -- edge also takes the subtract/compare carry chains out of the fire path.
    -- Only post-arm samples can fire (valid_width_r), and pretrig_len_r is
    -- latched with the arm, so the carried value is always this capture's.
    --
    pv_in <= since_arm_r
             when since_arm_r < resize(pretrig_len_r, since_arm_r'length)
             else resize(pretrig_len_r, since_arm_r'length);

    -- Ring overshoot (REA-T2.6): the samples stored while the trigger
    -- pipeline catches up (at most C_PIPE_STAGES) land past the trigger cell.
    -- With PRETRIG near DEPTH they overwrite the OLDEST pre-trigger cells. That
    -- can only hit a counted cell when since_arm is saturated at the fire edge
    -- (unsaturated means since_arm(s) + lag = since_arm(f) < DEPTH), and then
    -- PRETRIG_VALID takes the conservative cap DEPTH - 1 - C_PIPE_STAGES: it
    -- never vouches for an overwritten cell (it may under-count by the lag the
    -- decimation/qualification saved). The compare is registered; the fire
    -- edge only selects.
    pv_sat <= to_unsigned(G_DEPTH - 1 - C_PIPE_STAGES, pv_sat'length)
              when pv_over_r = '1'
              else resize(pretrig_len_r, pv_sat'length);

    p_pv_over : process (sample_clk_i, sample_rst_i)
    begin
        if sample_rst_i = '1' then
            pv_over_r <= '0';
        elsif rising_edge(sample_clk_i) then
            if to_integer(pretrig_len_r) > G_DEPTH - 1 - C_PIPE_STAGES then
                pv_over_r <= '1';
            else
                pv_over_r <= '0';
            end if;
        end if;
    end process;

    seq_final_fire <= '1' when (
        seq_enable_r = '1' and
        pipeline_final_valid = '1' and
        seq_state_r = to_unsigned(G_TRIG_STAGES - 1, C_SEQ_STATE_W) and
        seq_final_match(G_TRIG_STAGES - 1) = '1' and
        seq_counter_view(G_TRIG_STAGES - 1) + 1
            >= seq_count_target_view(G_TRIG_STAGES - 1)
    ) else '0';

    -- ── Status outputs ───────────────────────────────────────────
    armed_o         <= armed_r;
    triggered_o     <= triggered_r;
    done_o          <= done_r;
    overflow_o      <= overflow_r;
    trigger_o   <= trigger_out_r;
    wr_ptr_o    <= std_logic_vector(wr_ptr_r);
    trig_ptr_o  <= std_logic_vector(trig_ptr_r);
    start_ptr_o <= std_logic_vector(start_ptr_r);
    pretrig_valid_o <= std_logic_vector(pretrig_valid_r);

    -- ── DPRAM drive — sliding-window write enable. Note: NOT gated
    -- by `armed_r`. This is the architectural fix vs the naive design.
    -- v0.3: also gated by decim_tick so only every (decim_ratio+1)
    -- sample is stored. With decim_ratio=0 the tick is always 1 and
    -- behavior matches v0.1/v0.2 exactly. ───────────────────────
    store_sample <= '1' when (
        done_r = '0' and store_tick = '1' and not (
            armed_r = '1' and triggered_r = '1' and
            post_full = '1'
        )
    ) else '0';
    dpram_we_o   <= store_sample;
    dpram_addr_o <= std_logic_vector(wr_ptr_r);
    dpram_din_o  <= probe_i;

    -- Wide comparator data is atomic arm-time configuration. The resettable
    -- enable/state registers below keep it unobservable until this load.
    process (sample_clk_i)
    begin
        if rising_edge(sample_clk_i) then
            probe_prev_r <= probe_i;
            if arm_pulse_i = '1' then
                qual_values_r   <= qual_values_i;
                qual_masks_r    <= qual_masks_i;
                trig_value_r    <= trig_value_i;
                trig_mask_r     <= trig_mask_i;
                seq_value_r_flat <= seq_values_i;
                seq_mask_r_flat  <= seq_masks_i;
                cond_values_r   <= cond_values_i;
                cond_masks_r    <= cond_masks_i;
            end if;
        end if;
    end process;

    -- ── Capture FSM ──────────────────────────────────────────────
    process (sample_clk_i, sample_rst_i)
        -- REA-P2.11: window-full threshold and the fire-edge lag (low bits).
        variable v_thr   : unsigned(C_PTR_W downto 0);
        variable v_x_low : unsigned(C_PF_K - 1 downto 0);
    begin
        if sample_rst_i = '1' then
            armed_r        <= '0';
            triggered_r    <= '0';
            done_r         <= '0';
            overflow_r     <= '0';
            wr_ptr_r       <= (others => '0');
            trig_ptr_r     <= (others => '0');
            start_ptr_r    <= (others => '0');
            post_count_r   <= (others => '0');
            post_full_r    <= '0';
            -- POSTTRIG 0, no decimation/qualification: threshold 0.
            pf_thr_m1_r    <= (others => '0');
            pf_thr_small_r <= '1';
            pf_thr_low_r   <= (others => '0');
            pretrig_len_r  <= (others => '0');
            posttrig_len_r <= (others => '0');
            trig_mode_r    <= (others => '0');
            decim_ratio_r  <= (others => '0');
            decim_count_r  <= (others => '0');
            seq_enable_r   <= '0';
            seq_state_r    <= (others => '0');
            seq_count_target_r_flat <= (others => '0');
            seq_counter_r_flat      <= (others => '0');
            array_enable_r <= '0';
            -- Wide comparator configuration is loaded atomically on arm and
            -- ignored while its reset control enables remain low.
            cond_ops_r     <= (others => '0');
            cond_valid_r   <= (others => '0');
            ext_enable_r   <= '0';
            ext_and_r      <= '0';
            ext_trig_r     <= '0';
            trigger_out_r  <= '0';
            qual_enable_r  <= '0';
            qual_or_r      <= '0';
            qual_ops_r     <= (others => '0');
            qual_valid_r   <= (others => '0');

        elsif rising_edge(sample_clk_i) then

            -- Default: trigger_out is a 1-cycle pulse.
            trigger_out_r <= '0';

            -- External board-pin: register every cycle (RTL-P3.266). The pin
            -- is already sample_clk-synced in rr_rea_top; this is the local
            -- pipeline flop so the fold above sees a clean registered level.
            ext_trig_r <= ext_trigger_i;

            -- ── Free-running write pointer ─────────────────────
            -- REA-REQ-100/101: wr_ptr advances every cycle while
            -- !done, regardless of armed state. arm_pulse does NOT
            -- reset wr_ptr — pre-arm context is preserved.
            -- v0.3: also gated by decim_tick so wr_ptr only advances
            -- on stored samples (one per decim_ratio+1 cycles).
            if store_sample = '1' then
                wr_ptr_r <= wr_ptr_r + 1;
            end if;

            -- ── v0.3 decimation counter ────────────────────────
            -- Down-counter that wraps at decim_ratio. When the counter
            -- hits 0, decim_tick fires for one cycle (storing this
            -- sample), then the counter reloads to decim_ratio.
            -- arm_pulse resets the counter so each capture session
            -- starts on a clean tick boundary.
            -- REA-P2.7: with qualification on, the counter advances only on
            -- qualifying cycles, so decimation keeps every (N+1)-th QUALIFIED
            -- sample (REA-REQ-954). qual_ok is '1' when it is off.
            if done_r = '0' and qual_ok = '1' then
                if decim_count_r = 0 then
                    decim_count_r <= decim_ratio_r;
                else
                    decim_count_r <= decim_count_r - 1;
                end if;
            end if;

            -- ── reset_pulse: hard reset of capture state ───────
            if reset_pulse_i = '1' then
                armed_r       <= '0';
                triggered_r   <= '0';
                done_r        <= '0';
                overflow_r    <= '0';
                post_count_r  <= (others => '0');
                post_full_r   <= '0';
                trigger_out_r <= '0';
                -- NOTE: wr_ptr_r is NOT reset on reset_pulse for v0.1
                -- — keeping the buffer state alive across soft resets
                -- is consistent with sliding-window semantics. Hard
                -- buffer-clearing only happens via sample_rst.
            end if;

            -- ── arm_pulse: enable trigger watching ─────────────
            -- Latches config, but does NOT reset wr_ptr_r.
            -- RTL-T1.16: count samples actually STORED since this arm, so the
            -- host can tell which pre-trigger cells belong to this capture.
            -- Saturating: once a full window has been written every
            -- pre-trigger cell is contiguous and the exact count stops
            -- mattering.
            if store_sample = '1' and armed_r = '1' and triggered_r = '0'
               and since_arm_r /= to_unsigned(G_DEPTH, since_arm_r'length) then
                since_arm_r <= since_arm_r + 1;
            end if;

            if arm_pulse_i = '1' then
                armed_r        <= '1';
                triggered_r    <= '0';
                done_r         <= '0';
                -- Restart the contiguity count with the capture.
                since_arm_r     <= (others => '0');
                pretrig_valid_r <= (others => '0');
                post_count_r   <= (others => '0');
                post_full_r    <= '0';
                -- REA-P2.11: the window is full at post_count >= POSTTRIG, or
                -- > POSTTRIG when this capture is exact_count (qualified or
                -- decimated): threshold = POSTTRIG (+ 1), from the arm inputs
                -- so an external trigger in the first armed cycle sees it.
                -- Derived straight from POSTTRIG so no full-width increment
                -- sits in series with the exact decode (arm-input Fmax path):
                --   exact:  thr - 1 = POSTTRIG,       small = POSTTRIG < 2^K - 1
                --   else:   thr - 1 = POSTTRIG - 1 (sat), small = POSTTRIG < 2^K
                v_thr := resize(unsigned(posttrig_len_i), v_thr'length);
                if (G_QUAL_CONDS > 0 and qual_enable_i = '1')
                   or unsigned(decim_ratio_i) /= 0 then
                    pf_thr_m1_r  <= v_thr;
                    pf_thr_low_r <= resize(v_thr, C_PF_K) + 1;
                    if v_thr < 2 ** C_PF_K - 1 then
                        pf_thr_small_r <= '1';
                    else
                        pf_thr_small_r <= '0';
                    end if;
                else
                    if v_thr = 0 then
                        pf_thr_m1_r <= (others => '0');
                    else
                        pf_thr_m1_r <= v_thr - 1;
                    end if;
                    pf_thr_low_r <= resize(v_thr, C_PF_K);
                    if v_thr < 2 ** C_PF_K then
                        pf_thr_small_r <= '1';
                    else
                        pf_thr_small_r <= '0';
                    end if;
                end if;
                pretrig_len_r  <= unsigned(pretrig_len_i);
                posttrig_len_r <= unsigned(posttrig_len_i);
                trig_mode_r    <= trig_mode_i;
                decim_ratio_r  <= unsigned(decim_ratio_i);
                -- REA-REQ-606: arm_pulse resets seq_state to 0 and
                -- clears all counters; latches the per-stage config.
                seq_enable_r   <= seq_enable_i;
                seq_state_r    <= (others => '0');
                seq_count_target_r_flat <= seq_counts_i;
                seq_counter_r_flat      <= (others => '0');
                -- RTL-P3.647: latch the comparator-array config on arm too.
                array_enable_r <= array_enable_i;
                cond_ops_r     <= cond_ops_i;
                cond_valid_r   <= cond_valid_i;
                -- RTL-P3.266: latch external-trigger enable + combine mode on
                -- arm (quasi-static, like the other enables).
                ext_enable_r   <= ext_enable_i;
                ext_and_r      <= ext_and_i;
                -- REA-P2.7: the qualifier is arm-time config (REA-REQ-959).
                if G_QUAL_CONDS > 0 then
                    qual_enable_r <= qual_enable_i;
                    qual_or_r     <= qual_or_i;
                    qual_ops_r    <= qual_ops_i;
                    qual_valid_r  <= qual_valid_i;
                end if;
                -- Load count to 0 so the FIRST cycle after arm ticks
                -- (stores) — and subsequent ticks happen every
                -- (decim_ratio + 1) cycles. With decim_ratio=0 the
                -- counter reloads to 0 every cycle → tick every
                -- cycle (no decimation, matches v0.1/v0.2).
                decim_count_r  <= (others => '0');
                -- Overflow check: window doesn't fit in DEPTH.
                -- REA-T2.6 / REA-REQ-107: the up-to-C_PIPE_STAGES samples
                -- stored while the trigger pipeline catches up land past the
                -- trigger cell whatever POSTTRIG says, so the window really
                -- spans PRETRIG + max(POSTTRIG, C_PIPE_STAGES) + 1 cells.
                if to_integer(unsigned(pretrig_len_i))
                   + max_nat(to_integer(unsigned(posttrig_len_i)),
                             C_PIPE_STAGES) >= G_DEPTH then
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
            -- REA-T1.5: arm_pulse / reset_pulse take PRIORITY over trigger
            -- detection. Both write triggered_r/done_r/armed_r earlier in this
            -- process; without this guard a trigger (or post-trig completion,
            -- below) racing an arm/reset on the same cycle would win the later
            -- sequential write, leaving an inconsistent state (e.g. done=1 with
            -- triggered=0). The arm/reset branch must fully own that cycle.
            if armed_r = '1' and triggered_r = '0' and done_r = '0'
               and arm_pulse_i = '0' and reset_pulse_i = '0' then
                if local_fire_pipe = '1' or trigger_i = '1' then
                    triggered_r <= '1';
                    -- RTL-T1.16: freeze the contiguity figure AT the trigger.
                    -- Cells beyond this are pre-arm residue, not context.
                    --
                    -- Subtract C_PIPE_STAGES: trigger detection is pipelined,
                    -- so the fire lands C_PIPE_STAGES cycles AFTER the sample
                    -- that caused it (which is why trig_ptr_r comes from the
                    -- pointer pipeline, not from wr_ptr_r). Samples stored
                    -- during that latency sit AFTER the triggering sample and
                    -- are post-trigger, not pre-trigger context — counting
                    -- them overstated the valid window by exactly the pipeline
                    -- depth, which the sim caught as a constant +4 at
                    -- G_SAMPLE_W=12 / G_TRIG_CONDS=4.
                    if local_fire_pipe = '1' then
                        -- REA-T2.5: exact in every mode (see pv_in / pv_sat).
                        if since_arm_r = to_unsigned(G_DEPTH,
                                                     since_arm_r'length) then
                            pretrig_valid_r <= pv_sat;
                        else
                            pretrig_valid_r <= pipeline_final_pv;
                        end if;
                    elsif exact_count = '1' then
                        -- External trigger_i with qualification/decimation:
                        -- no pipeline lag (fire_lag = 0), since_arm counts
                        -- STORES (REA-REQ-955/960).
                        if since_arm_r < resize(pretrig_len_r,
                                                since_arm_r'length) then
                            pretrig_valid_r <= since_arm_r;
                        else
                            pretrig_valid_r <= resize(pretrig_len_r,
                                                      pretrig_valid_r'length);
                        end if;
                    elsif since_arm_r <= to_unsigned(C_PIPE_STAGES,
                                                  since_arm_r'length) then
                        pretrig_valid_r <= (others => '0');
                    elsif (since_arm_r - C_PIPE_STAGES)
                          < resize(pretrig_len_r, since_arm_r'length) then
                        pretrig_valid_r <= since_arm_r - C_PIPE_STAGES;
                    else
                        pretrig_valid_r <= resize(pretrig_len_r,
                                                  pretrig_valid_r'length);
                    end if;
                    -- REA-P2.11: v_x_low = the low C_PF_K bits of the value
                    -- loaded into post_count_r below (the trigger-pipeline
                    -- lag, <= C_PIPE_STAGES + 1, so the low bits ARE the
                    -- value), for the registered window-full compare.
                    v_x_low := (others => '0');
                    if local_fire_pipe = '1' then
                        trig_ptr_r <= local_fire_ptr;
                        v_x_low := resize(wr_ptr_r, C_PF_K)
                                   - resize(local_fire_ptr, C_PF_K);
                        if exact_count = '1' then
                            -- Cells written from the trigger position on,
                            -- including this edge's store (REA-REQ-955/960).
                            post_count_r <= resize(fire_lag, post_count_r'length)
                                            + unsigned'("" & store_sample);
                            v_x_low := v_x_low + unsigned'("" & store_sample);
                        else
                            post_count_r <= resize(wr_ptr_r - local_fire_ptr,
                                                   post_count_r'length);
                        end if;
                    else
                        trig_ptr_r <= wr_ptr_r;
                        post_count_r <= (others => '0');
                        if exact_count = '1' then
                            post_count_r(0) <= store_sample;
                            v_x_low(0) := store_sample;
                        end if;
                    end if;
                    if pf_thr_small_r = '1' and v_x_low >= pf_thr_low_r then
                        post_full_r <= '1';
                    else
                        post_full_r <= '0';
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
            -- REA-P2.7: counts STORED samples (store_tick = decim_tick while
            -- qualification is off). With qualification on the window closes
            -- on the cycle after its last store, without waiting for another
            -- qualifying cycle — a bus that falls silent must still finish the
            -- capture (REA-REQ-954). REA-T2.4: the same holds under decimation,
            -- so `done` rises the cycle after the last post-trigger store, not
            -- on the next decimation tick.
            if armed_r = '1' and triggered_r = '1' and done_r = '0'
               and (store_tick = '1' or exact_count = '1')
               and arm_pulse_i = '0' and reset_pulse_i = '0' then  -- REA-T1.5
                if post_full = '1' then
                    -- Done capturing the post-trigger window.
                    -- REA-REQ-104: start_ptr <= trig_ptr - pretrig_len
                    -- (mod DEPTH — natural wrap on PTR_W-bit subtract).
                    done_r      <= '1';
                    armed_r     <= '0';
                    start_ptr_r <= trig_ptr_r - pretrig_len_r;
                elsif store_tick = '1' then
                    post_count_r <= post_count_r + 1;
                    -- post_count_r + 1 >= threshold (REA-P2.11).
                    if post_count_r >= pf_thr_m1_r then
                        post_full_r <= '1';
                    else
                        post_full_r <= '0';
                    end if;
                end if;
            end if;

        end if;
    end process;

end architecture;
